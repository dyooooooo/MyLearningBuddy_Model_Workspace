"""Build and persist document IR after PDF convert.

Import cost cascade (second stage is Cursor SDK, not 35B VLM):

1. MinerU → blocks
2. math enrich + declarative layout rules (cheap; ~90% pages)
3. low-confidence pages → ``layout_escalate_pages`` in job meta for Cursor
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.models import CorpusImportJob
from app.services.corpus_symptoms import summarize_findings
from app.services.document_ir import DocumentBlock, render_page_html
from app.services.import_ir_store import load_blocks, save_blocks
from app.services.ir.layout import PageDecision
from app.services.ir.layout_enrich import enrich_page_blocks_slim_with_decision
from app.services.ir.math_enrich_blocks import enrich_page_blocks_math
from app.services.ir.mineru_adapter import mineru_content_list_to_ir
from app.services.ir.pdf_render_cache import PdfRenderCache
from app.services.ir.textbook_profile import default_profile_id, load_textbook_profile
from app.services.ir.vision_adapter import resolve_asset_urls
from app.services.import_pipeline_trace import (
    PIPELINE_PHASE_RENDER,
    pipeline_patch,
)
from app.services.source_manifest import build_source_manifest, save_source_manifest

logger = logging.getLogger(__name__)


def _finalize_page_blocks(
    page_blocks: list[DocumentBlock],
    *,
    profile: dict[str, Any],
    page_w: float,
    page_h: float,
) -> tuple[list[DocumentBlock], PageDecision | None]:
    """Math enrich → slim layout enrich for one page.

    Returns the layout decision so low-confidence pages can be queued for
    Cursor SDK (not 35B refine).
    """
    page_blocks = enrich_page_blocks_math(page_blocks)
    return enrich_page_blocks_slim_with_decision(
        page_blocks, profile=profile, page_w=page_w, page_h=page_h
    )


def _decision_summary(decision: PageDecision) -> dict[str, Any]:
    return {
        "score": round(float(decision.score), 3),
        "low_keys": list(decision.low_confidence_keys),
    }


def _page_symptom_findings(
    page_blocks: list[DocumentBlock], html: str, *, page_no: int
) -> list[dict[str, Any]]:
    """命名症状。与低置信页列表并列，不混在一起。

    两者回答的是不同问题：低置信页是「版式判定自己没把握」的数值兜底，症状是
    「这一处出了什么毛病」的命名结论，只有后者能对上历史成功路径。
    """
    from app.services.import_ir_validation import run_page_checks

    try:
        return [f.model_dump() for f in run_page_checks(page_blocks, html, page_no=page_no)]
    except Exception as exc:  # noqa: BLE001 — 检测永远不能阻断导入
        logger.warning("symptom checks failed on page %d: %s", page_no, exc)
        return []


def _run_finalize_streaming(
    blocks: list[DocumentBlock],
    *,
    pdf_bytes: bytes,
    profile_id: str,
    progress_cb=None,
    page_html_cb=None,
    job_id: str = "",
    only_pages: set[int] | None = None,
    render_cache: PdfRenderCache | None = None,
    debug_job: CorpusImportJob | None = None,
    raw_blocks: list[dict[str, Any]] | None = None,
) -> tuple[list[DocumentBlock], dict[str, Any]]:
    """Per-page pipeline: math → layout rules → render → stream.

    Never calls 35B refine. Pages with ``PageDecision.escalate`` are recorded
    in the returned meta for Cursor SDK intake.
    """
    from app.config import settings
    from app.services.pipeline_telemetry import ImportCanceled, raise_if_canceled

    profile = load_textbook_profile(profile_id)
    escalate_pages: list[int] = []
    escalate_summary: dict[str, Any] = {}
    symptom_findings: list[dict[str, Any]] = []

    by_page: dict[int, list[DocumentBlock]] = {}
    for b in blocks:
        by_page.setdefault(b.page_no, []).append(b)
    all_pages = sorted(by_page.keys())
    target_pages = [
        p for p in all_pages if only_pages is None or p in only_pages
    ]
    total = len(target_pages)

    owns_cache = render_cache is None
    if owns_cache:
        render_cache = PdfRenderCache(pdf_bytes)

    progress_phase = PIPELINE_PHASE_RENDER
    if progress_cb:
        try:
            progress_cb(pipeline_patch(progress_phase, refine_total=total))
        except Exception:
            pass

    try:
        for idx, page_no in enumerate(target_pages, start=1):
            if job_id:
                raise_if_canceled(
                    job_id, verify_corpus_job=(idx == 1 or idx % 5 == 0)
                )
            if progress_cb:
                try:
                    progress_cb(
                        pipeline_patch(
                            progress_phase,
                            refine_progress=f"{idx}/{total}",
                            refine_page=idx,
                            refine_total=total,
                        )
                    )
                except Exception:
                    pass

            source_blocks = by_page[page_no]
            pw, ph = render_cache.page_size(page_no)
            page_blocks, decision = _finalize_page_blocks(
                source_blocks,
                profile=profile,
                page_w=pw,
                page_h=ph,
            )

            if decision is not None and decision.escalate:
                escalate_pages.append(page_no)
                escalate_summary[str(page_no)] = _decision_summary(decision)
                logger.info(
                    "layout cascade: page %d queued for Cursor SDK "
                    "(score=%.3f, low=%d)",
                    page_no,
                    decision.score,
                    len(decision.low_confidence_keys),
                )

            by_page[page_no] = page_blocks

            if page_html_cb is not None or (
                debug_job is not None and settings.corpus_debug_dump
            ):
                from app.services.corpus_blank_page import mark_blank_html
                from app.services.corpus_debug_bundle import dump_page_debug

                html = render_page_html(page_blocks, page_no, profile_id=profile_id)
                if any(bool((b.meta or {}).get("blank")) for b in page_blocks):
                    html = mark_blank_html(html)
                # 症状检测跑在这里而不是单独一趟：HTML 这一刻已经渲染好了，
                # 再走一遍等于把整本书重渲一次。
                symptom_findings.extend(
                    _page_symptom_findings(page_blocks, html, page_no=page_no)
                )
                if page_html_cb is not None:
                    try:
                        page_html_cb(page_no, html)
                    except ImportCanceled:
                        raise
                    except Exception as e:
                        msg = str(e).lower()
                        if any(
                            s in msg
                            for s in (
                                "has been deleted",
                                "import job not found",
                                "import job deleted",
                                "object is not bound",
                            )
                        ):
                            raise ImportCanceled(
                                f"import job aborted during page_html_cb page={page_no}: {e}"
                            ) from e
                        logger.warning("page_html_cb error page %d: %s", page_no, e)
                if debug_job is not None and settings.corpus_debug_dump:
                    page_jpeg = None
                    try:
                        assert render_cache is not None
                        page_jpeg = render_cache.page_jpeg(page_no, dpi=120)
                    except Exception:
                        page_jpeg = None
                    try:
                        dump_page_debug(
                            debug_job,
                            page_no=page_no,
                            blocks=page_blocks,
                            html=html,
                            page_jpeg=page_jpeg,
                            raw_blocks=raw_blocks,
                        )
                    except Exception as e:
                        logger.warning(
                            "corpus debug dump error page %d: %s", page_no, e
                        )
    finally:
        if owns_cache:
            render_cache.close()

    logger.info(
        "layout cascade: %d/%d pages queued for Cursor SDK (%s)",
        len(escalate_pages),
        total,
        ",".join(str(p) for p in escalate_pages[:20]) or "none",
    )

    out = [b for page_no in all_pages for b in by_page[page_no]]
    out.sort(key=lambda b: (b.page_no, b.order))
    symptom_findings = _map_findings_to_patterns(symptom_findings)
    summary = summarize_findings(symptom_findings)
    logger.info(
        "symptom checks: %d findings over %d known / %d unknown pages",
        summary["total"],
        len(summary["known_pages"]),
        len(summary["unknown_pages"]),
    )
    cascade_meta = {
        # Compat empty refine fields — import never runs 35B refine.
        "refine_prompt_version": "",
        "page_refine_chunks": [],
        "refine_errors": [],
        "layout_escalate_pages": escalate_pages,
        "layout_escalate_summary": escalate_summary,
        # 曾经恒为 "cursor_sdk"（谁也没读过）。现在表示这批疑点有没有成功路径。
        "layout_escalate_target": _coverage_label(summary),
        "symptom_findings": symptom_findings,
        "symptom_summary": summary,
    }
    return out, cascade_meta


def _coverage_label(summary: dict[str, Any]) -> str:
    """这批症状有没有历史成功路径：none / known / unknown / mixed。"""
    known = bool(summary.get("known_pages"))
    unknown = bool(summary.get("unknown_pages"))
    if known and unknown:
        return "mixed"
    if known:
        return "known"
    if unknown:
        return "unknown"
    return "none"


def _map_findings_to_patterns(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """挂上模式与历史成功路径。**确定性完成，不依赖 Agent 在线。**

    用户只点「开始解析」不开 AI 时也要看得见「这是老问题」，所以映射放在 worker
    里做完写进元数据，Agent 只负责 playbook 档的执行。
    """
    if not findings:
        return findings
    try:
        from app.db.session import get_session_factory
        from app.services.corpus_symptoms import enrich_findings

        with get_session_factory()() as session:
            # count_hits=True：这是检测器真命中，要计进误报率的分母。
            out = enrich_findings(session, findings, count_hits=True)
            session.commit()
            return out
    except Exception as exc:  # noqa: BLE001 — 映射不了就退回裸 findings
        logger.warning("symptom mapping skipped: %s", exc)
        return findings


# Back-compat alias for any external callers still using the old name.
_run_refine_and_finalize_streaming = _run_finalize_streaming


def build_ir_from_parse(
    *,
    engine: str,
    raw_blocks: list[dict[str, Any]] | None,
    vision_pages: list[dict[str, Any]] | None,
    pdf_bytes: bytes,
    asset_prefix: str,
    profile_id: str | None = None,
    progress_cb=None,
    page_html_cb=None,
    job_id: str = "",
    debug_job: CorpusImportJob | None = None,
    total_pages_hint: int | None = None,
    expected_pages: set[int] | None = None,
) -> list[DocumentBlock]:
    """Convert MinerU parse output to DocumentBlock IR and finalize layout.

    Does not call 35B refine. Low-confidence pages are listed in
    ``get_last_refine_meta()["layout_escalate_pages"]`` for Cursor SDK.
    """
    global _last_refine_meta, _last_missing_pages
    _last_refine_meta = {}
    _last_missing_pages = []
    pid = profile_id or default_profile_id()
    _ = vision_pages

    # Note: an EMPTY raw_blocks list is valid MinerU output (e.g. a segment of
    # only blank pages) — placeholders must still be generated, so guard on
    # ``is not None`` rather than truthiness.
    if raw_blocks is not None:
        blocks = mineru_content_list_to_ir(
            raw_blocks, engine=engine, asset_prefix=asset_prefix
        )
        blocks = resolve_asset_urls(blocks)

        from app.services.corpus_blank_page import ensure_blank_pages

        missing: list[int] = []
        blocks = ensure_blank_pages(
            blocks,
            pdf_bytes=pdf_bytes,
            total_pages=total_pages_hint,
            expected_pages=expected_pages,
            missing_pages=missing,
        )
        _last_missing_pages = missing
        if missing:
            logger.warning(
                "import missing page content job=%s pages=%s (IR empty, PDF not blank)",
                job_id,
                missing,
            )

        blocks, cascade_meta = _run_finalize_streaming(
            blocks,
            pdf_bytes=pdf_bytes,
            profile_id=pid,
            progress_cb=progress_cb,
            page_html_cb=page_html_cb,
            job_id=job_id,
            debug_job=debug_job,
            raw_blocks=raw_blocks if isinstance(raw_blocks, list) else None,
        )
        _last_refine_meta = cascade_meta
        return blocks

    # ``vision_pages`` path removed together with the vision_ocr engine — the
    # MinerU line always produces ``raw_blocks``.
    return []


# Module-level cascade meta written by build_ir_from_parse (legacy name kept).
_last_refine_meta: dict[str, Any] = {}

# Module-level missing-page report written by build_ir_from_parse
_last_missing_pages: list[int] = []


def get_last_refine_meta() -> dict[str, Any]:
    return dict(_last_refine_meta)


def get_last_missing_pages() -> list[int]:
    return list(_last_missing_pages)


def persist_job_ir(
    session,
    job: CorpusImportJob,
    blocks: list[DocumentBlock],
    *,
    pdf_bytes: bytes | None = None,
    parse_engine: str = "",
    pages_rendered: set[int] | None = None,
) -> None:
    """Save blocks.json + manifest; render only pages not already streamed.

    The per-page pipeline streams final HTML during convert, so by default
    (``pages_rendered`` covering every page) this performs no re-render — it only
    persists blocks.json, the source manifest, and job meta (incl. escalate list).
    """
    from app.services.corpus_import_incremental import (
        ImportProgressWriter,
        _job_meta,
        _set_job_meta,
        upsert_page_draft_chunk,
    )

    if not blocks:
        return

    meta = _job_meta(job)
    engine = parse_engine or str(meta.get("parse_engine") or "")
    profile_id = str(meta.get("textbook_profile") or default_profile_id())

    blocks_key = save_blocks(job, blocks)
    # Freeze per-page MinerU IR once so Studio can roll back after edits.
    try:
        from app.services.ir.mineru_baseline import save_mineru_baseline

        for page_no in sorted({b.page_no for b in blocks}):
            page_blocks = [b for b in blocks if b.page_no == page_no]
            save_mineru_baseline(job, page_no, page_blocks, overwrite=False)
    except Exception as exc:  # pragma: no cover
        logger.warning("mineru baseline seed skipped job=%s: %s", job.id, exc)
    manifest = build_source_manifest(
        job,
        blocks,
        pdf_bytes=pdf_bytes or b"",
        blocks_key=blocks_key,
        parse_engine=engine,
        profile_id=profile_id,
    )
    manifest_key = save_source_manifest(job, manifest)

    pages = sorted({b.page_no for b in blocks})
    total = len(pages)
    already = pages_rendered or set()
    pending = [p for p in pages if p not in already]
    writer = ImportProgressWriter(session, job)
    writer.patch(pipeline_patch(PIPELINE_PHASE_RENDER, refine_total=total))
    for page_no in pending:
        page_html = render_page_html(blocks, page_no, profile_id=profile_id)
        if page_html.strip():
            upsert_page_draft_chunk(
                session,
                job,
                page_no=page_no,
                page_content=page_html,
                total_pages=total,
                engine=engine,
                content_format="html",
            )

    cascade_meta = get_last_refine_meta()
    meta["blocks_key"] = blocks_key
    meta["block_count"] = len(blocks)
    meta["has_structure"] = True
    meta["content_format"] = "html"
    meta["source_manifest_key"] = manifest_key
    meta["textbook_profile"] = profile_id
    meta["layout_finalize_at"] = _now_iso_safe()
    meta["refine_done_at"] = meta["layout_finalize_at"]  # legacy UI key
    _merge_escalate_into_meta(meta, cascade_meta)
    # Track pages where MinerU dropped real content (empty IR, non-blank PDF).
    # Accumulate across extends and heal once a page finally gets blocks.
    missing_new = get_last_missing_pages()
    prev_missing = {int(p) for p in (meta.get("missing_pages") or [])}
    have_pages = {int(b.page_no) for b in blocks}
    merged_missing = sorted((prev_missing | set(missing_new)) - have_pages)
    if merged_missing:
        meta["missing_pages"] = merged_missing
    else:
        meta.pop("missing_pages", None)
    job.markdown = ""
    _set_job_meta(session, job, meta)
    writer.maybe_commit(force=True)
    logger.info(
        "import IR persisted job=%s blocks=%d pages=%d escalate=%d format=html",
        job.id,
        len(blocks),
        total,
        len(meta.get("layout_escalate_pages") or []),
    )


def _merge_escalate_into_meta(
    meta: dict[str, Any],
    cascade_meta: dict[str, Any],
    *,
    only_pages: set[int] | None = None,
) -> None:
    """Merge cascade escalate list into job meta (union across partial rebuilds)."""
    meta["page_refine_chunks"] = cascade_meta.get("page_refine_chunks") or []
    meta["refine_errors"] = cascade_meta.get("refine_errors") or []
    meta["refine_prompt_version"] = cascade_meta.get("refine_prompt_version") or ""

    new_pages = [int(p) for p in (cascade_meta.get("layout_escalate_pages") or [])]
    new_summary = dict(cascade_meta.get("layout_escalate_summary") or {})
    new_findings = list(cascade_meta.get("symptom_findings") or [])

    if only_pages is None:
        meta["layout_escalate_pages"] = sorted(new_pages)
        meta["layout_escalate_summary"] = {
            str(k): v for k, v in new_summary.items()
        }
        meta["symptom_findings"] = new_findings
    else:
        prev_pages = {int(p) for p in (meta.get("layout_escalate_pages") or [])}
        # Pages we just rebuilt: drop old escalate flag unless still escalated.
        prev_pages -= set(only_pages)
        prev_pages |= set(new_pages)
        prev_summary = dict(meta.get("layout_escalate_summary") or {})
        for p in only_pages:
            prev_summary.pop(str(p), None)
        prev_summary.update({str(k): v for k, v in new_summary.items()})
        meta["layout_escalate_pages"] = sorted(prev_pages)
        meta["layout_escalate_summary"] = prev_summary
        # 局部重建同理：只换被重建那些页的症状，别的页保留上一次的结论。
        kept = [
            f
            for f in (meta.get("symptom_findings") or [])
            if int(f.get("page_no") or 0) not in only_pages
        ]
        meta["symptom_findings"] = kept + new_findings

    summary = summarize_findings(list(meta.get("symptom_findings") or []))
    meta["symptom_summary"] = summary
    meta["layout_escalate_target"] = _coverage_label(summary)


def rerender_page_html(
    blocks: list[DocumentBlock],
    page_no: int,
    *,
    profile_id: str | None = None,
) -> str:
    return render_page_html(blocks, page_no, profile_id=profile_id or default_profile_id())


def get_job_ir(job: CorpusImportJob) -> list[DocumentBlock] | None:
    return load_blocks(job)


def refresh_job_symptom_findings(job: CorpusImportJob) -> dict[str, Any]:
    """按当前中间表示整本重跑命名症状，覆盖导入时快照。

    ``patch_block`` / 单页 rebuild 不会自动重算全书症状。自动改每轮结束必须
    走这里，编排器才能凭活数据决定能不能把 ``fullbook_autofix_done`` 置真。
    """
    from datetime import datetime, timezone

    from app.util.json_util import dumps, loads

    meta = loads(job.meta_json, {}) or {}
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    blocks = get_job_ir(job)
    if not blocks:
        raise ValueError("job has no IR to recheck")
    by_page: dict[int, list[DocumentBlock]] = {}
    for block in blocks:
        by_page.setdefault(block.page_no, []).append(block)
    findings: list[dict[str, Any]] = []
    for page_no in sorted(by_page):
        html = rerender_page_html(blocks, page_no, profile_id=profile_id)
        findings.extend(
            _page_symptom_findings(by_page[page_no], html, page_no=page_no)
        )
    findings = _map_findings_to_patterns(findings)
    summary = summarize_findings(findings)
    meta["symptom_findings"] = findings
    meta["symptom_summary"] = summary
    meta["layout_escalate_target"] = _coverage_label(summary)
    meta["symptom_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    job.meta_json = dumps(meta)
    logger.info(
        "symptom refresh job=%s total=%s known_pages=%s unknown_pages=%s",
        job.id,
        summary["total"],
        len(summary["known_pages"]),
        len(summary["unknown_pages"]),
    )
    return summary


def rebuild_job_ir(
    session,
    job: CorpusImportJob,
    *,
    progress_cb=None,
    pages: list[int] | None = None,
) -> tuple[list[DocumentBlock], dict[str, Any]]:
    """Re-run layout enrich + HTML on stored blocks (no MinerU re-parse, no 35B).

    ``pages=None`` rebuilds every page; ``pages=[12, 16]`` only re-enriches and
    re-renders those pages while leaving the rest untouched.
    """
    from app.clients.minio_client import get_object_bytes
    from app.services.corpus_import_incremental import (
        ImportProgressWriter,
        _job_meta,
        _set_job_meta,
        upsert_page_draft_chunk,
    )

    pdf_bytes = get_object_bytes(job.minio_key) if job.minio_key else b""
    if not pdf_bytes:
        raise ValueError("import job PDF missing in MinIO")

    blocks = load_blocks(job)
    if not blocks:
        raise ValueError("import job has no blocks.json; run convert first")

    meta = _job_meta(job)
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    engine = str(meta.get("parse_engine") or "mineru_cloud")

    only_pages: set[int] | None = None
    if pages:
        available = {b.page_no for b in blocks}
        only_pages = {int(p) for p in pages if int(p) in available}
        if not only_pages:
            raise ValueError(f"none of pages {pages} exist in job blocks")

    writer = ImportProgressWriter(session, job)
    total_pages = len({b.page_no for b in blocks})

    def _page_html_cb(page_no: int, html: str) -> None:
        if html.strip():
            upsert_page_draft_chunk(
                session,
                job,
                page_no=page_no,
                page_content=html,
                total_pages=total_pages,
                engine=engine,
                content_format="html",
            )
            session.flush()

    blocks, cascade_meta = _run_finalize_streaming(
        blocks,
        pdf_bytes=pdf_bytes,
        profile_id=profile_id,
        progress_cb=progress_cb,
        page_html_cb=_page_html_cb,
        only_pages=only_pages,
    )

    blocks_key = save_blocks(job, blocks)

    meta["blocks_key"] = blocks_key
    meta["block_count"] = len(blocks)
    meta["has_structure"] = True
    meta["content_format"] = "html"
    meta["textbook_profile"] = profile_id
    meta["layout_finalize_at"] = _now_iso_safe()
    meta["refine_done_at"] = meta["layout_finalize_at"]
    if only_pages:
        meta["rebuilt_pages"] = sorted(only_pages)
    _merge_escalate_into_meta(meta, cascade_meta, only_pages=only_pages)
    _set_job_meta(session, job, meta)
    writer.maybe_commit(force=True)
    logger.info(
        "import IR rebuilt job=%s blocks=%d pages=%s escalate=%d",
        job.id,
        len(blocks),
        "all" if only_pages is None else sorted(only_pages),
        len(meta.get("layout_escalate_pages") or []),
    )
    return blocks, cascade_meta


def _now_iso_safe() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
