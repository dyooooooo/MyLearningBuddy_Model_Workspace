from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes
from app.db.models import CorpusEdge, CorpusImportJob, CorpusNode, CorpusTagLink
from app.services.corpus_import_incremental import (
    ImportProgressWriter,
    ensure_import_textbook_shell,
    make_page_done_handler,
    run_import_structure_merge,
)
from app.services.corpus_publish import upsert_draft_node
from app.services.corpus_tree import collect_subtree_ids
from app.services.md_textbook_parser import parse_textbook_markdown
from app.services.pdf_convert import convert_pdf_bytes
from app.services.import_pipeline_trace import (
    PIPELINE_PHASE_ADAPTER,
    PIPELINE_PHASE_MINIO,
    parse_phase_for_engine,
    pipeline_patch,
)
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_import_job(
    session: Session,
    *,
    minio_key: str,
    subject: str,
    grade_band: str,
    label: str = "",
    textbook_id: str = "",
    source_channel: str = "pdf_import",
    parse_engine: str = "mineru_cloud",
    page_limit: int | None = None,
    page_ranges: str | None = None,
    edition_id: str,
    textbook_profile: str | None = None,
) -> CorpusImportJob:
    from app.schemas.parse_job_config import ParseJobConfig

    eid = (edition_id or textbook_profile or "").strip()
    if not eid:
        raise ValueError("edition_id is required")
    pr = (page_ranges or "").strip() or None
    config = ParseJobConfig(
        engine=parse_engine,
        edition_id=eid,
        textbook_profile=eid,
        page_limit=None if pr else page_limit,
        page_ranges=pr,
    )
    job = CorpusImportJob(
        id=str(uuid.uuid4()),
        status="uploaded",
        source_channel=source_channel,
        minio_key=minio_key,
        subject=subject,
        grade_band=grade_band,
        label=label,
        textbook_node_id=textbook_id,
        edition_id=eid,
        meta_json=dumps(
            {
                # Flat fields kept for backward-compat (older workers / queries)
                "parse_engine": parse_engine,
                "edition_id": eid,
                "textbook_profile": eid,
                "page_ranges": pr,
                "page_limit": None if pr else page_limit,
                # Authoritative nested config
                "parse_config": config.model_dump(),
                **pipeline_patch(PIPELINE_PHASE_MINIO),
            }
        ),
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    session.add(job)
    session.flush()
    logger.info(
        "import job created id=%s subject=%s grade=%s label=%r minio_key=%s engine=%s "
        "page_limit=%s page_ranges=%s",
        job.id,
        subject,
        grade_band,
        label,
        minio_key,
        parse_engine,
        page_limit,
        pr,
    )
    return job


def run_import_convert(session: Session, job_id: str) -> CorpusImportJob:
    """Incremental convert: shell + per-page draft chunks + throttled commits."""
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    t0 = time.perf_counter()
    logger.info("import convert start id=%s minio_key=%s", job_id, job.minio_key)
    job.status = "converting"
    job.updated_at = _now_iso()
    session.flush()

    writer = ImportProgressWriter(session, job)
    writer.patch(pipeline_patch(PIPELINE_PHASE_MINIO))

    try:
        raw = get_object_bytes(job.minio_key)
        if not raw:
            raise RuntimeError(f"MinIO object not found: {job.minio_key}")
        logger.info("import convert fetched id=%s bytes=%d", job_id, len(raw))

        from app.schemas.parse_job_config import ParseJobConfig

        _job_config = ParseJobConfig.from_meta(loads(job.meta_json, {}))
        parse_engine = _job_config.engine
        # Each engine owns its OWN parse phase — no longer forced to mineru_parse.
        _parse_phase = parse_phase_for_engine(parse_engine)

        writer.patch(pipeline_patch(_parse_phase))

        ensure_import_textbook_shell(session, job)
        session.commit()
        job = session.get(CorpusImportJob, job_id)
        if job is None:
            raise ValueError(f"import job not found after shell: {job_id}")

        writer = ImportProgressWriter(session, job)
        page_done = make_page_done_handler(session, job, writer)

        def _progress(patch: dict) -> None:
            # Cooperative cancel: progress_cb fires per-page for ALL three
            # engines, so checking here gives every line a clean stop point.
            from app.services.pipeline_telemetry import raise_if_canceled

            raise_if_canceled(job_id, verify_corpus_job=True)
            if "pipeline_phase" not in patch:
                patch = {**pipeline_patch(_parse_phase), **patch}
            writer.patch(patch)

        asset_prefix = ""
        if job.minio_key:
            from app.services.import_assets import asset_prefix_from_minio_key

            asset_prefix = asset_prefix_from_minio_key(job.minio_key)
        # NOTE: the 35B layout line (vision_layout_35b) is FULLY ISOLATED and
        # runs through app.import_35b.service.run_convert via its own queue/API.
        # It intentionally does NOT flow through this MinerU import path anymore.
        md, meta = convert_pdf_bytes(
            raw,
            filename=job.minio_key.split("/")[-1],
            engine=parse_engine,
            progress_cb=_progress,
            page_done_cb=page_done,
            asset_prefix=asset_prefix,
            page_limit=_job_config.page_limit,
            page_ranges=_job_config.page_ranges,
        )
        from app.services.corpus_import_incremental import upsert_page_draft_chunk
        from app.services.import_ir_pipeline import build_ir_from_parse, persist_job_ir
        from datetime import datetime, timezone

        from app.services.corpus_blank_page import parse_page_ranges

        _expected_pages = parse_page_ranges(_job_config.page_ranges)
        if not _expected_pages and _job_config.page_limit:
            _expected_pages = set(range(1, int(_job_config.page_limit) + 1))

        writer.patch(pipeline_patch(PIPELINE_PHASE_ADAPTER))

        mineru_meta = {
            "mineru_done_at": datetime.now(timezone.utc).isoformat(),
            "content_list_source": meta.get("content_list_source"),
            "page_ranges": meta.get("page_ranges"),
            "page_limit": meta.get("page_limit") or _job_config.page_limit,
            "mineru_raw_keys": meta.get("mineru_raw_keys") or {},
            "total_pages": int(
                meta.get("total_pages") or meta.get("page_count") or meta.get("ocr_total") or 0
            ),
        }
        writer.patch({k: v for k, v in mineru_meta.items() if v is not None})

        # Derive expected page count from convert meta (MinerU / vision_ocr both set it)
        _total_pages_hint = int(meta.get("total_pages") or meta.get("ocr_total") or 0)
        if not _total_pages_hint and _expected_pages:
            # All-blank segment: MinerU reports 0 pages; use the requested span.
            _total_pages_hint = len(_expected_pages)

        pages_streamed: set[int] = set()

        def _page_html_cb(page_no: int, html: str) -> None:
            """Stream each page's final HTML to DB as soon as it's ready."""
            from app.services.pipeline_telemetry import raise_if_canceled

            # Redis cancel only here — the page loop already does periodic DB verify.
            raise_if_canceled(job_id)
            upsert_page_draft_chunk(
                session,
                job,
                page_no=page_no,
                page_content=html,
                total_pages=_total_pages_hint,
                engine=parse_engine,
                content_format="html",
            )
            pages_streamed.add(page_no)
            session.flush()
            writer.on_page_committed()

        ir_blocks = build_ir_from_parse(
            engine=parse_engine,
            raw_blocks=meta.get("raw_blocks"),
            vision_pages=meta.get("vision_pages"),
            pdf_bytes=raw,
            asset_prefix=asset_prefix,
            progress_cb=_progress,
            page_html_cb=_page_html_cb,
            job_id=job_id,
            debug_job=job,
            total_pages_hint=_total_pages_hint or None,
            expected_pages=_expected_pages or None,
            profile_id=_job_config.edition_id,
        )
        if ir_blocks:
            persist_job_ir(
                session,
                job,
                ir_blocks,
                pdf_bytes=raw,
                parse_engine=parse_engine,
                pages_rendered=pages_streamed,
            )
            job = session.get(CorpusImportJob, job_id)
            if job is None:
                raise ValueError(f"import job not found after IR: {job_id}")
            md = job.markdown
            ir_meta = loads(job.meta_json, {})
            meta["blocks_key"] = ir_meta.get("blocks_key")
            meta["block_count"] = ir_meta.get("block_count")
            meta["has_structure"] = True
            meta["content_format"] = ir_meta.get("content_format", "html")
            meta["source_manifest_key"] = ir_meta.get("source_manifest_key")
        else:
            job.markdown = md
        job.status = "parsed"
        # The full MinerU content_list lives in MinIO blocks.json — keep the
        # bulky raw parse payloads out of the DB meta row.
        meta.pop("raw_blocks", None)
        meta.pop("vision_pages", None)
        writer.patch(meta)
        writer.maybe_commit(force=True)
        logger.info(
            "import convert done id=%s md_len=%d elapsed=%.2fs pages=%s",
            job_id,
            len(md),
            time.perf_counter() - t0,
            loads(job.meta_json, {}).get("chunk_progress"),
        )
        return job
    except Exception as e:
        job = session.get(CorpusImportJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(e)[:4000]
            job.updated_at = _now_iso()
            session.flush()
        logger.exception(
            "import convert failed id=%s elapsed=%.2fs",
            job_id,
            time.perf_counter() - t0,
        )
        raise


def _purge_textbook_subtree(session: Session, textbook_id: str) -> int:
    """Remove prior draft nodes under textbook before re-chunk (legacy batch path)."""
    if not textbook_id:
        return 0
    ids = collect_subtree_ids(session, textbook_id)
    if textbook_id not in ids:
        ids.insert(0, textbook_id)
    if len(ids) <= 1:
        return 0
    for nid in ids:
        session.query(CorpusEdge).filter(
            (CorpusEdge.from_id == nid) | (CorpusEdge.to_id == nid)
        ).delete(synchronize_session=False)
        session.query(CorpusTagLink).filter(CorpusTagLink.node_id == nid).delete(synchronize_session=False)

    deleted = session.query(CorpusNode).filter(CorpusNode.id.in_(ids)).delete(synchronize_session=False)
    session.flush()
    logger.info("import purge subtree textbook=%s nodes=%d", textbook_id, deleted)
    return int(deleted)


def run_import_chunk(session: Session, job_id: str) -> CorpusImportJob:
    """Legacy batch chunk — purges subtree. Prefer structure merge after incremental convert."""
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    if not job.markdown.strip():
        raise ValueError("job has no markdown; run convert first")

    meta = loads(job.meta_json, {})
    if meta.get("pages_ready"):
        return run_import_structure_merge(session, job_id)

    t0 = time.perf_counter()
    logger.info(
        "import chunk start id=%s md_len=%d textbook_node_id=%s",
        job_id,
        len(job.markdown),
        job.textbook_node_id or "",
    )

    tb_id = job.textbook_node_id or f"tb:import:{job.subject}:{job.grade_band}:{job.id[:8]}"
    _purge_textbook_subtree(session, tb_id)
    textbook, sections = parse_textbook_markdown(
        job.markdown,
        textbook_id=tb_id,
        subject=job.subject,
        grade_band=job.grade_band,
        prefix=tb_id,
        textbook_label=job.label,
    )

    upsert_draft_node(
        session,
        node_id=textbook["id"],
        node_type="textbook",
        subject=job.subject,
        grade_band=job.grade_band,
        label=job.label or textbook["label"],
        summary=job.label or textbook["summary"],
        full_text=textbook.get("text", ""),
        tags=textbook.get("tags", []),
        links=textbook.get("links", {}),
        meta={"doc_type": "textbook"},
        source_channel=job.source_channel,
    )
    job.textbook_node_id = textbook["id"]
    node_ids = [textbook["id"]]

    for sec in sections:
        sec_links: dict = {"textbook_id": textbook["id"], "section_id": sec.section_id}
        if sec.parent_section_id:
            sec_links["parent_section_id"] = sec.parent_section_id
        sec_meta = {**sec.meta, "level": sec.level}
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
        node_ids.append(sec.section_id)
        for ch in sec.chunks:
            ch_meta = ch.get("meta") or {"doc_type": "chunk"}
            upsert_draft_node(
                session,
                node_id=ch["id"],
                node_type="chunk",
                subject=job.subject,
                grade_band=job.grade_band,
                label=ch["label"],
                summary=ch["summary"],
                full_text=ch["text"],
                tags=[f"grade_band:{job.grade_band}", "chunk_kind:import"],
                links={
                    "section_id": ch["section_id"],
                    "textbook_id": textbook["id"],
                },
                meta=ch_meta,
                source_channel=job.source_channel,
            )
            node_ids.append(ch["id"])

    meta = loads(job.meta_json, {})
    meta["chunk_count"] = len(node_ids)
    meta["node_ids"] = node_ids
    job.meta_json = dumps(meta)
    job.status = "chunked"
    job.updated_at = _now_iso()
    session.flush()
    logger.info(
        "import chunk done id=%s textbook=%s nodes=%d elapsed=%.2fs",
        job_id,
        job.textbook_node_id,
        len(node_ids),
        time.perf_counter() - t0,
    )
    return job


def run_import_extend(session: Session, job_id: str, page_ranges: str) -> CorpusImportJob:
    """Append MinerU OCR for ``page_ranges`` into an existing job (merge IR by page)."""
    from app.services.import_ir_store import load_blocks
    from app.services.import_ir_pipeline import build_ir_from_parse, persist_job_ir
    from app.services.corpus_import_incremental import upsert_page_draft_chunk
    from app.services.corpus_blank_page import parse_page_ranges

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    pr = (page_ranges or "").strip()
    if not pr:
        raise ValueError("page_ranges is required for extend")

    t0 = time.perf_counter()
    logger.info("import extend start id=%s page_ranges=%s", job_id, pr)
    meta = loads(job.meta_json, {})
    # The API recorded the real pre-extend status before resetting to uploaded.
    prev_status = str(meta.pop("prev_status", "") or job.status)
    job.status = "converting"
    job.error = ""
    job.updated_at = _now_iso()
    meta["pending_page_ranges"] = pr
    meta["extend_mode"] = True
    job.meta_json = dumps(meta)
    session.flush()

    writer = ImportProgressWriter(session, job)
    try:
        raw = get_object_bytes(job.minio_key)
        if not raw:
            raise RuntimeError(f"MinIO object not found: {job.minio_key}")

        from app.schemas.parse_job_config import ParseJobConfig

        _job_config = ParseJobConfig.from_meta(loads(job.meta_json, {}))
        parse_engine = _job_config.engine
        _parse_phase = parse_phase_for_engine(parse_engine)
        writer.patch(pipeline_patch(_parse_phase))

        asset_prefix = ""
        if job.minio_key:
            from app.services.import_assets import asset_prefix_from_minio_key

            asset_prefix = asset_prefix_from_minio_key(job.minio_key)

        page_done = make_page_done_handler(session, job, writer)

        def _progress(patch: dict) -> None:
            from app.services.pipeline_telemetry import raise_if_canceled

            raise_if_canceled(job_id, verify_corpus_job=True)
            if "pipeline_phase" not in patch:
                patch = {**pipeline_patch(_parse_phase), **patch}
            writer.patch(patch)

        md, meta_conv = convert_pdf_bytes(
            raw,
            filename=job.minio_key.split("/")[-1],
            engine=parse_engine,
            progress_cb=_progress,
            page_done_cb=page_done,
            asset_prefix=asset_prefix,
            page_ranges=pr,
        )
        writer.patch(pipeline_patch(PIPELINE_PHASE_ADAPTER))
        writer.patch(
            {
                "page_ranges": pr,
                "last_extend_page_ranges": pr,
                "content_list_source": meta_conv.get("content_list_source"),
                "mineru_raw_keys": meta_conv.get("mineru_raw_keys") or {},
            }
        )

        pages_streamed: set[int] = set()
        _total_hint = int(meta_conv.get("total_pages") or meta_conv.get("ocr_total") or 0)
        if not _total_hint:
            # All-blank segment: MinerU reports 0 pages; use the requested span.
            _total_hint = len(parse_page_ranges(pr))

        def _page_html_cb(page_no: int, html: str) -> None:
            from app.services.pipeline_telemetry import raise_if_canceled

            raise_if_canceled(job_id)
            upsert_page_draft_chunk(
                session,
                job,
                page_no=page_no,
                page_content=html,
                total_pages=_total_hint,
                engine=parse_engine,
                content_format="html",
            )
            pages_streamed.add(page_no)
            session.flush()
            writer.on_page_committed()

        new_ir = build_ir_from_parse(
            engine=parse_engine,
            raw_blocks=meta_conv.get("raw_blocks"),
            vision_pages=meta_conv.get("vision_pages"),
            pdf_bytes=raw,
            asset_prefix=asset_prefix,
            progress_cb=_progress,
            page_html_cb=_page_html_cb,
            job_id=job_id,
            debug_job=job,
            total_pages_hint=_total_hint or None,
            expected_pages=parse_page_ranges(pr) or None,
            profile_id=_job_config.edition_id,
        )
        existing = load_blocks(job) or []
        new_pages = {b.page_no for b in (new_ir or [])}
        merged = [b for b in existing if b.page_no not in new_pages]
        merged.extend(new_ir or [])
        merged.sort(key=lambda b: (int(b.page_no), str(getattr(b, "id", "") or "")))
        if merged:
            persist_job_ir(
                session,
                job,
                merged,
                pdf_bytes=raw,
                parse_engine=parse_engine,
                pages_rendered=pages_streamed,
            )
        job = session.get(CorpusImportJob, job_id)
        assert job is not None
        job.status = "parsed"
        meta2 = loads(job.meta_json, {})
        # Compare-and-delete: only clear the pending range this run consumed,
        # so a concurrently queued extend is never silently overwritten.
        if meta2.get("pending_page_ranges") == pr:
            meta2.pop("pending_page_ranges", None)
            meta2.pop("extend_mode", None)
        segs = list(meta2.get("extended_page_ranges") or [])
        if pr not in segs:
            segs.append(pr)
        meta2["extended_page_ranges"] = segs
        job.meta_json = dumps(meta2)
        job.updated_at = _now_iso()
        writer.maybe_commit(force=True)
        # structure merge so tree stays consistent after append
        job = run_import_structure_merge(session, job_id)
        logger.info(
            "import extend done id=%s page_ranges=%s prev=%s elapsed=%.2fs",
            job_id,
            pr,
            prev_status,
            time.perf_counter() - t0,
        )
        return job
    except Exception as e:
        job = session.get(CorpusImportJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(e)[:4000]
            job.updated_at = _now_iso()
            session.flush()
        logger.exception("import extend failed id=%s", job_id)
        raise


def process_import_job(session: Session, job_id: str) -> CorpusImportJob:
    t0 = time.perf_counter()
    logger.info("import pipeline start id=%s", job_id)
    job0 = session.get(CorpusImportJob, job_id)
    if job0 is None:
        raise ValueError(f"import job not found: {job_id}")
    meta0 = loads(job0.meta_json, {})
    pending_ext = str(meta0.get("pending_page_ranges") or "").strip()
    if meta0.get("extend_mode") and pending_ext:
        job = run_import_extend(session, job_id, pending_ext)
        logger.info(
            "import pipeline (extend) done id=%s status=%s elapsed=%.2fs",
            job_id,
            job.status,
            time.perf_counter() - t0,
        )
        return job

    job = run_import_convert(session, job_id)
    if job.status == "parsed":
        # Replay prior user corrections for the same book (fingerprint match).
        try:
            from app.services.corpus_corrections import apply_matched_corrections

            applied = apply_matched_corrections(session, job)
            if applied.get("applied"):
                logger.info(
                    "import corrections replayed id=%s applied=%s match=%s",
                    job_id,
                    applied.get("applied"),
                    applied.get("match"),
                )
            session.flush()
        except Exception:
            logger.exception("import correction replay failed id=%s", job_id)
        job = run_import_structure_merge(session, job_id)
    logger.info(
        "import pipeline done id=%s status=%s textbook=%s elapsed=%.2fs",
        job_id,
        job.status,
        job.textbook_node_id or "",
        time.perf_counter() - t0,
    )
    return job
