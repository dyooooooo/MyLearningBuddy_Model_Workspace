"""Incremental PDF import — per-page draft chunks + throttled progress commits."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import CorpusImportJob
from app.services.corpus_publish import upsert_draft_node
from app.services.document_ir import html_to_plain_text, normalize_figure_caption
from app.services.import_ir_store import load_blocks
from app.services.import_pipeline_trace import (
    PIPELINE_PHASE_DONE,
    PIPELINE_PHASE_TREE,
    pipeline_patch,
)
from app.services.md_textbook_parser import parse_textbook_markdown
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

COMMIT_EVERY_PAGES = 3
COMMIT_EVERY_SECONDS = 2.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_meta(job: CorpusImportJob) -> dict:
    return loads(job.meta_json, {})


def _set_job_meta(session: Session, job: CorpusImportJob, meta: dict) -> None:
    job.meta_json = dumps(meta)
    job.updated_at = _now_iso()
    session.flush()


def textbook_id_for_job(job: CorpusImportJob) -> str:
    if job.textbook_node_id:
        return job.textbook_node_id
    return f"tb:import:{job.subject}:{job.grade_band}:{job.id[:8]}"


def pages_section_id(textbook_id: str) -> str:
    return f"section:{textbook_id}:pages"


def page_chunk_id(textbook_id: str, page_no: int) -> str:
    return f"chunk:{textbook_id}:p{page_no:04d}"


def ensure_import_textbook_shell(session: Session, job: CorpusImportJob) -> str:
    """Create textbook + placeholder pages section before OCR starts."""
    tb_id = textbook_id_for_job(job)
    sec_id = pages_section_id(tb_id)
    meta = _job_meta(job)
    node_ids = list(meta.get("node_ids") or [])

    upsert_draft_node(
        session,
        node_id=tb_id,
        node_type="textbook",
        subject=job.subject,
        grade_band=job.grade_band,
        label=job.label or tb_id,
        summary=job.label or "",
        full_text="",
        tags=[f"grade_band:{job.grade_band}", f"subject:{job.subject}"],
        links={"textbook_id": tb_id},
        meta={"doc_type": "textbook", "import_job_id": job.id},
        source_channel=job.source_channel,
    )
    upsert_draft_node(
        session,
        node_id=sec_id,
        node_type="section",
        subject=job.subject,
        grade_band=job.grade_band,
        label="逐页导入",
        summary="导入进行中 — 按 PDF 页生成的草稿块",
        full_text="",
        tags=[f"grade_band:{job.grade_band}"],
        links={"textbook_id": tb_id, "section_id": sec_id},
        meta={"doc_type": "section", "level": 1, "import_job_id": job.id, "placeholder": True},
        source_channel=job.source_channel,
    )

    for nid in (tb_id, sec_id):
        if nid not in node_ids:
            node_ids.append(nid)

    meta["node_ids"] = node_ids
    meta.setdefault("pages_ready", [])
    job.textbook_node_id = tb_id
    _set_job_meta(session, job, meta)
    logger.info("import shell ready job=%s textbook=%s", job.id, tb_id)
    return tb_id


class ImportProgressWriter:
    """Throttled meta updates + periodic session.commit for UI polling."""

    def __init__(self, session: Session, job: CorpusImportJob) -> None:
        self.session = session
        self.job = job
        self._last_commit = time.monotonic()
        self._pages_since_commit = 0

    def patch(self, patch: dict) -> None:
        prev = _job_meta(self.job)
        prev_phase = prev.get("pipeline_phase")
        meta = {**prev, **patch}
        _set_job_meta(self.session, self.job, meta)

        new_phase = meta.get("pipeline_phase")
        prev_refine = int(prev.get("refine_page") or 0)
        new_refine = int(meta.get("refine_page") or 0)
        total = int(meta.get("refine_total") or meta.get("ocr_total") or 0)

        # Phase changes always commit. Per-page progress is throttled so render_html
        # does not pay a full Postgres commit on every page (~150ms+ each).
        phase_changed = bool(new_phase and new_phase != prev_phase)
        refine_moved = new_refine > 0 and new_refine != prev_refine
        due = (
            time.monotonic() - self._last_commit >= COMMIT_EVERY_SECONDS
            or self._pages_since_commit >= COMMIT_EVERY_PAGES
            or (total > 0 and new_refine >= total)
        )
        needs_commit = phase_changed or (refine_moved and due)
        if needs_commit:
            try:
                self.session.commit()
                self._last_commit = time.monotonic()
                self._pages_since_commit = 0
            except Exception:  # pragma: no cover
                self.session.rollback()
        elif refine_moved:
            self._pages_since_commit += 1

        # Mirror live phase + sub-progress to the telemetry layer (worker only).
        try:
            from app.services import pipeline_telemetry

            page = new_refine
            detail = str(
                meta.get("refine_progress")
                or meta.get("chunk_progress")
                or meta.get("ocr_progress")
                or ""
            )
            phase_for_telemetry = new_phase or prev.get("pipeline_phase") or ""
            if phase_for_telemetry or new_refine > 0:
                pipeline_telemetry.update_phase(
                    str(phase_for_telemetry), detail, page=page, total=total
                )
        except Exception:  # pragma: no cover
            pass

        # Studio SSE: publish when we commit or pages_ready / status meaningfully changes.
        if needs_commit or phase_changed:
            try:
                from app.clients.redis_client import publish_import_event

                pages_ready = meta.get("pages_ready")
                publish_import_event(
                    {
                        "type": "job.progress",
                        "line": "mineru",
                        "job_id": self.job.id,
                        "status": self.job.status,
                        "chunk_progress": meta.get("chunk_progress"),
                        "missing_pages": meta.get("missing_pages"),
                        "pages_ready": pages_ready,
                        "pipeline_phase": meta.get("pipeline_phase"),
                    }
                )
                prev_pr = prev.get("pages_ready")
                if pages_ready is not None and pages_ready != prev_pr:
                    publish_import_event(
                        {
                            "type": "page.ready",
                            "line": "mineru",
                            "job_id": self.job.id,
                            "pages_ready": pages_ready,
                            "status": self.job.status,
                        }
                    )
            except Exception:  # pragma: no cover
                pass

    def maybe_commit(self, *, force: bool = False) -> None:
        if not force and self._pages_since_commit < COMMIT_EVERY_PAGES:
            if time.monotonic() - self._last_commit < COMMIT_EVERY_SECONDS:
                return
        self.session.commit()
        self._last_commit = time.monotonic()
        self._pages_since_commit = 0

    def on_page_committed(self) -> None:
        self._pages_since_commit += 1
        self.maybe_commit()


def upsert_page_draft_chunk(
    session: Session,
    job: CorpusImportJob,
    *,
    page_no: int,
    page_md: str = "",
    page_content: str = "",
    total_pages: int,
    engine: str = "",
    content_format: str = "markdown",
    durable: bool = False,
) -> str:
    """Upsert one page-level draft chunk; update job meta.

    When ``durable=True`` (page/block re-recognize), commit immediately so the
    Studio reload and fingerprint replay always see the new HTML.
    """
    body = (page_content or page_md).strip()
    if not body:
        logger.info(
            "import page chunk skipped job=%s page=%d (empty content)",
            job.id,
            page_no,
        )
        return ""

    tb_id = job.textbook_node_id or textbook_id_for_job(job)
    sec_id = pages_section_id(tb_id)
    chunk_id = page_chunk_id(tb_id, page_no)
    label = f"第 {page_no} 页"
    summary = html_to_plain_text(body)[:240] if content_format == "html" else body[:240]

    upsert_draft_node(
        session,
        node_id=chunk_id,
        node_type="chunk",
        subject=job.subject,
        grade_band=job.grade_band,
        label=label,
        summary=summary,
        full_text=body,
        tags=[f"grade_band:{job.grade_band}", "chunk_kind:page_import"],
        links={
            "textbook_id": tb_id,
            "section_id": sec_id,
        },
        meta={
            "doc_type": "chunk",
            "source_page": page_no,
            "import_job_id": job.id,
            "chunk_kind": "page_import",
            "parse_engine": engine or _job_meta(job).get("parse_engine", ""),
            "content_format": content_format,
        },
        source_channel=job.source_channel,
    )

    meta = _job_meta(job)
    pages_ready = list(meta.get("pages_ready") or [])
    if page_no not in pages_ready:
        pages_ready.append(page_no)
        pages_ready.sort()

    node_ids = list(meta.get("node_ids") or [])
    for nid in (tb_id, sec_id, chunk_id):
        if nid and nid not in node_ids:
            node_ids.append(nid)

    meta["pages_ready"] = pages_ready
    meta["node_ids"] = node_ids
    meta["chunk_progress"] = f"{len(pages_ready)}/{total_pages or len(pages_ready)}"
    meta["last_committed_page"] = page_no
    _set_job_meta(session, job, meta)

    # Immediate durable write for live refine / user re-recognize.
    if durable or meta.get("pipeline_phase") == "refine_35b":
        try:
            session.commit()
        except Exception:  # pragma: no cover
            session.rollback()
            raise

    logger.info(
        "import page chunk job=%s page=%d/%s id=%s chars=%d format=%s durable=%s",
        job.id,
        page_no,
        total_pages,
        chunk_id,
        len(body),
        content_format,
        durable or meta.get("pipeline_phase") == "refine_35b",
    )
    return chunk_id


def append_job_markdown(job: CorpusImportJob, page_md: str) -> None:
    block = page_md.strip()
    if not block:
        return
    if job.markdown.strip():
        job.markdown = f"{job.markdown.rstrip()}\n\n---\n\n{block}"
    else:
        job.markdown = block


def make_page_done_handler(
    session: Session,
    job: CorpusImportJob,
    writer: ImportProgressWriter,
) -> Callable[[int, str, int], None]:
    """Factory for per-page OCR callback."""

    def on_page_done(page_no: int, page_md: str, total_pages: int) -> None:
        append_job_markdown(job, page_md)
        chunk_id = upsert_page_draft_chunk(
            session,
            job,
            page_no=page_no,
            page_md=page_md,
            total_pages=total_pages,
            engine=str(_job_meta(job).get("parse_engine") or ""),
        )
        if chunk_id:
            writer.on_page_committed()

    return on_page_done


def run_import_structure_merge(session: Session, job_id: str) -> CorpusImportJob:
    """Attach chapter/section nodes; keep page-level draft chunks (no purge)."""
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")

    # Last cooperative cancel checkpoint before the structure merge writes nodes.
    from app.services.pipeline_telemetry import raise_if_canceled

    raise_if_canceled(job_id, verify_corpus_job=True)

    blocks = load_blocks(job)
    if not blocks and not job.markdown.strip():
        raise ValueError("job has no markdown or IR blocks; run convert first")

    job.status = "chunking"
    job.updated_at = _now_iso()
    meta = {**_job_meta(job), **pipeline_patch(PIPELINE_PHASE_TREE)}
    _set_job_meta(session, job, meta)
    session.flush()

    tb_id = job.textbook_node_id or textbook_id_for_job(job)
    meta = _job_meta(job)
    node_ids = list(meta.get("node_ids") or [])

    if blocks:
        from app.services.corpus_tree_from_ir import build_sections_from_ir, section_for_page

        refine_chunks = meta.get("page_refine_chunks") or []
        logger.info("import structure merge via IR job=%s blocks=%d", job_id, len(blocks))
        textbook, ir_sections = build_sections_from_ir(
            blocks,
            textbook_id=tb_id,
            subject=job.subject,
            grade_band=job.grade_band,
            textbook_label=job.label,
            refine_chunks=refine_chunks if refine_chunks else None,
        )
        sections = ir_sections
    else:
        logger.warning(
            "import structure merge fallback regex job=%s (no blocks.json)",
            job_id,
        )
        textbook, parsed_sections = parse_textbook_markdown(
            job.markdown,
            textbook_id=tb_id,
            subject=job.subject,
            grade_band=job.grade_band,
            prefix=tb_id,
            textbook_label=job.label,
        )
        sections = parsed_sections

    # PDF textbook import only: deepen coarse TOC by knowledge point (not KB pipeline).
    structure_report: dict = {}
    from app.config import settings as _settings

    if _settings.textbook_toc_deepen_enabled and blocks:
        from app.services.textbook_toc_deepen import deepen_textbook_sections

        meta = {**_job_meta(job), "structure_status": "structuring"}
        _set_job_meta(session, job, meta)
        session.flush()
        sections, structure_report = deepen_textbook_sections(
            sections,
            prefix=tb_id,
            textbook_label=job.label or textbook.get("label", ""),
        )
        logger.info(
            "import toc deepen job=%s status=%s deepened=%s review=%s leaves=%s",
            job_id,
            structure_report.get("structure_status"),
            len(structure_report.get("deepened_parents") or []),
            len(structure_report.get("needs_structure_review") or []),
            structure_report.get("leaf_count"),
        )

    upsert_draft_node(
        session,
        node_id=textbook["id"],
        node_type="textbook",
        subject=job.subject,
        grade_band=job.grade_band,
        label=job.label or textbook["label"],
        summary=job.label or textbook["summary"],
        full_text=textbook.get("text", job.markdown[:8000]),
        tags=textbook.get("tags", []),
        links=textbook.get("links", {}),
        meta={"doc_type": "textbook", "import_job_id": job.id},
        source_channel=job.source_channel,
    )

    section_ids: list[str] = []
    for sec in sections:
        sec_links: dict = {"textbook_id": textbook["id"], "section_id": sec.section_id}
        if sec.parent_section_id:
            sec_links["parent_section_id"] = sec.parent_section_id
        sec_meta = {**sec.meta, "level": sec.level, "import_job_id": job.id}
        if getattr(sec, "start_page", None):
            sec_meta["start_page"] = sec.start_page
        if sec.meta.get("end_page") is not None:
            sec_meta["end_page"] = sec.meta.get("end_page")
        if sec.meta.get("block_ids"):
            sec_meta["block_ids"] = sec.meta.get("block_ids")
        if sec.meta.get("grounded") is not None:
            sec_meta["grounded"] = sec.meta.get("grounded")
        upsert_draft_node(
            session,
            node_id=sec.section_id,
            node_type="section",
            subject=job.subject,
            grade_band=job.grade_band,
            label=sec.label,
            summary=sec.summary,
            full_text=sec.text,
            tags=[f"grade_band:{job.grade_band}"],
            links=sec_links,
            meta=sec_meta,
            source_channel=job.source_channel,
        )
        section_ids.append(sec.section_id)
        if sec.section_id not in node_ids:
            node_ids.append(sec.section_id)

        for ch in sec.chunks:
            chunk_kind = str((ch.get("meta") or {}).get("chunk_kind") or "toc_leaf")
            ch_meta = {**(ch.get("meta") or {}), "doc_type": "chunk", "chunk_kind": chunk_kind}
            ch_meta["import_job_id"] = job.id
            block_ids = ch_meta.get("block_ids") or []
            if block_ids:
                ch_meta["meta_hint"] = f"IR · {len(block_ids)} blocks"
            ch_links: dict = {
                "section_id": ch["section_id"],
                "textbook_id": textbook["id"],
            }
            proposed_kp = ch_meta.get("proposed_primary_kp") or sec.meta.get("proposed_primary_kp")
            if proposed_kp:
                ch_links["primary_kp"] = proposed_kp
                ch_meta["kp_id"] = proposed_kp
            upsert_draft_node(
                session,
                node_id=ch["id"],
                node_type="chunk",
                subject=job.subject,
                grade_band=job.grade_band,
                label=ch["label"],
                summary=ch["summary"],
                full_text=ch["text"],
                tags=[f"grade_band:{job.grade_band}", f"chunk_kind:{chunk_kind}"],
                links=ch_links,
                meta=ch_meta,
                source_channel=job.source_channel,
            )
            if ch["id"] not in node_ids:
                node_ids.append(ch["id"])

    if blocks:
        _assign_page_chunks_by_ir(session, job, sections)
    else:
        _assign_page_chunks_to_sections(session, job, section_ids or [pages_section_id(tb_id)])

    meta["structure_merged"] = True
    meta["section_ids"] = section_ids
    meta["chunk_count"] = len(node_ids)
    meta["node_ids"] = node_ids
    if blocks:
        meta["block_count"] = len(blocks)
    if structure_report:
        meta["structure_status"] = structure_report.get(
            "structure_status", "structure_ready"
        )
        meta["toc_deepen"] = structure_report
        # Always overwrite so a successful deepen clears a prior review flag.
        meta["needs_structure_review"] = list(
            structure_report.get("needs_structure_review") or []
        )
        if not meta["needs_structure_review"]:
            meta.pop("needs_structure_review", None)
    else:
        meta.setdefault("structure_status", "structure_ready")
    job.textbook_node_id = textbook["id"]
    # Terminal success phase — distinct from "still running" / "queued".
    meta = {**meta, **pipeline_patch(PIPELINE_PHASE_DONE)}
    job.meta_json = dumps(meta)
    job.status = "chunked"
    job.updated_at = _now_iso()
    session.flush()
    logger.info(
        "import structure merge done id=%s sections=%d nodes=%d ir=%s",
        job_id,
        len(section_ids),
        len(node_ids),
        bool(blocks),
    )
    return job


def purge_textbook_structure_keep_pages(session: Session, textbook_id: str) -> dict:
    """Drop TOC/semantic nodes under a textbook; keep textbook + 逐页导入 + page chunks.

    Used by the main-line structure-merge re-run so next books / re-imports share
    one path (not a one-off script).
    """
    import re
    from collections import deque

    from app.db.models import CorpusEdge, CorpusNode, CorpusTagLink

    if not textbook_id:
        return {"deleted": 0, "kept_pages": 0}
    page_re = re.compile(r":p\d{4}$")
    pages_sec = pages_section_id(textbook_id)
    keep = {textbook_id, pages_sec}
    ids = [textbook_id]
    seen = {textbook_id}
    q: deque[str] = deque([textbook_id])
    while q:
        pid = q.popleft()
        for e in (
            session.query(CorpusEdge)
            .filter(
                CorpusEdge.from_id == pid,
                CorpusEdge.rel_type.in_(("contains_section", "contains_chunk")),
            )
            .all()
        ):
            if e.to_id in seen:
                continue
            seen.add(e.to_id)
            ids.append(e.to_id)
            child = session.get(CorpusNode, e.to_id)
            if child and child.node_type in ("textbook", "section"):
                q.append(e.to_id)

    delete_ids: list[str] = []
    kept_pages = 0
    for nid in ids:
        if nid in keep:
            continue
        node = session.get(CorpusNode, nid)
        if node is None:
            continue
        meta = loads(node.meta_json, {}) or {}
        kind = str(meta.get("chunk_kind") or "")
        if node.node_type == "chunk" and (
            kind == "page_import" or page_re.search(nid or "")
        ):
            keep.add(nid)
            kept_pages += 1
            continue
        delete_ids.append(nid)

    for nid in delete_ids:
        session.query(CorpusEdge).filter(
            (CorpusEdge.from_id == nid) | (CorpusEdge.to_id == nid)
        ).delete(synchronize_session=False)
        session.query(CorpusTagLink).filter(CorpusTagLink.node_id == nid).delete(
            synchronize_session=False
        )
    deleted = 0
    if delete_ids:
        deleted = (
            session.query(CorpusNode)
            .filter(CorpusNode.id.in_(delete_ids))
            .delete(synchronize_session=False)
        )
    session.query(CorpusEdge).filter(
        CorpusEdge.from_id == textbook_id,
        CorpusEdge.rel_type == "contains_section",
        CorpusEdge.to_id != pages_sec,
    ).delete(synchronize_session=False)
    session.flush()
    logger.info(
        "purge textbook structure keep pages tb=%s deleted=%d kept_pages=%d",
        textbook_id,
        deleted,
        kept_pages,
    )
    return {"deleted": int(deleted), "kept_pages": kept_pages, "kept": len(keep)}


def rerun_import_structure_merge(
    session: Session,
    job_id: str,
    *,
    purge: bool = True,
) -> CorpusImportJob:
    """Main-line re-structure: optional purge of stale TOC, then structure merge + deepen.

    Same path new books take after OCR (`run_import_structure_merge`). Call this
    (or POST /import/jobs/{id}/structure-merge) instead of ad-hoc scripts.
    """
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    tb_id = (job.textbook_node_id or textbook_id_for_job(job) or "").strip()
    if purge and tb_id:
        purge_textbook_structure_keep_pages(session, tb_id)
        session.flush()
    return run_import_structure_merge(session, job_id)


def _assign_page_chunks_by_ir(
    session: Session,
    job: CorpusImportJob,
    sections: list,
) -> None:
    """Map page chunks to IR sections by start_page (replaces even-split heuristic)."""
    from app.db.models import CorpusNode
    from app.services.corpus_tree_from_ir import section_for_page

    tb_id = job.textbook_node_id or ""
    if not tb_id or not sections:
        return

    meta = _job_meta(job)
    pages_ready = sorted(int(p) for p in (meta.get("pages_ready") or []))
    if not pages_ready:
        return

    for page_no in pages_ready:
        chunk_id = page_chunk_id(tb_id, page_no)
        node = session.get(CorpusNode, chunk_id)
        if node is None:
            continue
        sec_id = section_for_page(sections, page_no) or pages_section_id(tb_id)
        node_meta = loads(node.meta_json, {})
        links = dict(node_meta.get("links") or {})
        links["section_id"] = sec_id
        links["textbook_id"] = tb_id
        upsert_draft_node(
            session,
            node_id=chunk_id,
            node_type="chunk",
            subject=job.subject,
            grade_band=job.grade_band,
            label=node.label or f"第 {page_no} 页",
            summary=node.summary or "",
            full_text=node.full_text or "",
            tags=loads(node.tags_json, []),
            links=links,
            meta={**node_meta, "doc_type": "chunk", "source_page": page_no},
            source_channel=job.source_channel,
        )


def _assign_page_chunks_to_sections(
    session: Session,
    job: CorpusImportJob,
    section_ids: list[str],
) -> None:
    """Heuristic: map page chunks to sections by page order."""
    from app.db.models import CorpusNode

    tb_id = job.textbook_node_id or ""
    if not tb_id or not section_ids:
        return

    meta = _job_meta(job)
    pages_ready = sorted(int(p) for p in (meta.get("pages_ready") or []))
    if not pages_ready:
        return

    max_page = max(pages_ready)
    n_sec = len(section_ids)
    pages_per = max(1, (max_page + n_sec - 1) // n_sec)

    for page_no in pages_ready:
        chunk_id = page_chunk_id(tb_id, page_no)
        node = session.get(CorpusNode, chunk_id)
        if node is None:
            continue
        sec_idx = min(n_sec - 1, (page_no - 1) // pages_per)
        sec_id = section_ids[sec_idx]
        node_meta = loads(node.meta_json, {})
        links = dict(node_meta.get("links") or {})
        links["section_id"] = sec_id
        links["textbook_id"] = tb_id
        upsert_draft_node(
            session,
            node_id=chunk_id,
            node_type="chunk",
            subject=job.subject,
            grade_band=job.grade_band,
            label=node.label or f"第 {page_no} 页",
            summary=node.summary or "",
            full_text=node.full_text or "",
            tags=loads(node.tags_json, []),
            links=links,
            meta={**node_meta, "doc_type": "chunk", "source_page": page_no},
            source_channel=job.source_channel,
        )


def _caption_lines(raw: object) -> str:
    if isinstance(raw, list):
        lines = [str(x).strip() for x in raw if str(x).strip()]
        return "\n".join(lines)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""


def _block_image_markdown(block: dict) -> str:
    img_path = block.get("img_path") or block.get("image_path") or block.get("src") or ""
    if not isinstance(img_path, str) or not img_path.strip():
        return ""
    alt_parts: list[str] = []
    for key in (
        "image_caption",
        "table_caption",
        "caption",
        "image_footnote",
        "table_footnote",
    ):
        cap = _caption_lines(block.get(key))
        if cap:
            alt_parts.append(cap)
    btype = str(block.get("type") or "图")
    raw_alt = " · ".join(alt_parts) if alt_parts else ""
    if btype in ("image", "chart"):
        alt = normalize_figure_caption(raw_alt) or "插图"
    else:
        alt = raw_alt or {"equation": "公式", "chart": "图表", "table": "表格"}.get(btype, "图")
    md = f"![{alt}]({img_path.strip()})"
    cap = normalize_figure_caption(raw_alt) if btype in ("image", "chart") else raw_alt
    if cap and cap != alt:
        return f"{md}\n\n*{cap}*"
    if cap and btype in ("image", "chart"):
        return f"{md}\n\n*{cap}*"
    return md


def _mineru_block_plain_text(block: dict) -> str:
    """Extract plain text from v1 flat or v2 nested content_list blocks."""
    text = block.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    content = block.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, dict):
        for key in (
            "title_content",
            "paragraph_content",
            "math_content",
            "code_content",
            "algorithm_content",
            "page_aside_text_content",
        ):
            val = content.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, list):
                parts = []
                for item in val:
                    if isinstance(item, dict):
                        c = item.get("content")
                        if isinstance(c, str) and c.strip():
                            parts.append(c.strip())
                    elif isinstance(item, str) and item.strip():
                        parts.append(item.strip())
                if parts:
                    return "".join(parts)
        items = content.get("list_items")
        if isinstance(items, list):
            lines = []
            for item in items:
                if isinstance(item, str) and item.strip():
                    lines.append(f"- {item.strip()}")
                elif isinstance(item, dict):
                    c = item.get("content") or item.get("text")
                    if isinstance(c, str) and c.strip():
                        lines.append(f"- {c.strip()}")
            if lines:
                return "\n".join(lines)
    return ""


def _mineru_block_img_path(block: dict) -> str:
    for key in ("img_path", "image_path", "src"):
        val = block.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    content = block.get("content")
    if isinstance(content, dict):
        for key in ("img_path", "image_path", "src", "path"):
            val = content.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        # content_list_v2 nested path
        src = content.get("image_source")
        if isinstance(src, dict):
            for key in ("path", "img_path", "image_path", "src"):
                val = src.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        if isinstance(src, str) and src.strip():
            return src.strip()
    return ""


def mineru_blocks_to_page_markdown(blocks: list[dict]) -> str:
    """Render MinerU content_list blocks for one page to markdown (text + tables + figures)."""
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "")
        # Enrich image path for helpers that only look at top-level keys.
        img = _mineru_block_img_path(block)
        if img and not block.get("img_path"):
            block = {**block, "img_path": img}

        if btype in ("image", "chart", "equation", "equation_interline"):
            img_md = _block_image_markdown(block)
            if img_md:
                parts.append(img_md)
            text = _mineru_block_plain_text(block)
            if text:
                parts.append(text)
            continue

        if btype == "table":
            content = block.get("content") if isinstance(block.get("content"), dict) else {}
            cap = _caption_lines(block.get("table_caption") or content.get("table_caption"))
            if cap:
                parts.append(cap)
            table = (
                block.get("table_body")
                or block.get("html")
                or content.get("table_body")
                or content.get("html")
                or ""
            )
            if isinstance(table, str) and table.strip():
                parts.append(table.strip())
            img_md = _block_image_markdown(block)
            if img_md:
                parts.append(img_md)
            foot = _caption_lines(block.get("table_footnote") or content.get("table_footnote"))
            if foot:
                parts.append(foot)
            continue

        text = _mineru_block_plain_text(block)
        if text:
            parts.append(text)

    return "\n\n".join(parts)


def group_mineru_blocks_by_page(blocks: list[dict]) -> dict[int, list[dict]]:
    """Group blocks by page_idx (0-based in MinerU → store as 1-based page_no)."""
    grouped: dict[int, list[dict]] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw_idx = block.get("page_idx")
        if isinstance(raw_idx, int):
            page_no = raw_idx + 1 if raw_idx >= 0 else 1
        else:
            page_no = 1
        grouped.setdefault(page_no, []).append(block)
    return dict(sorted(grouped.items()))
