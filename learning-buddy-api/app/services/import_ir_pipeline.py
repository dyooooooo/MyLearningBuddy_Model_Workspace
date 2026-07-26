"""Build and persist document IR after PDF convert."""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.db.models import CorpusImportJob
from app.schemas.mineru_refine import REFINE_PROMPT_VERSION, RefineAllPagesResult
from app.services.document_ir import DocumentBlock, render_page_html
from app.services.import_ir_store import load_blocks, save_blocks
from app.services.ir.apply_refine import apply_page_refine
from app.services.ir.layout_enrich import enrich_page_blocks_slim
from app.services.ir.math_enrich_blocks import enrich_page_blocks_math
from app.services.ir.mineru_adapter import mineru_content_list_to_ir
from app.services.ir.mineru_page_refine import (
    get_refine_mode,
    get_refine_parallel,
    prompt_version_for_mode,
    refine_page,
    refine_result_to_meta_chunks,
)
from app.services.ir.pdf_render_cache import PdfRenderCache
from app.services.ir.textbook_profile import default_profile_id, load_textbook_profile
from app.services.ir.vision_adapter import resolve_asset_urls
from app.services.import_pipeline_trace import (
    PIPELINE_PHASE_REFINE,
    PIPELINE_PHASE_RENDER,
    pipeline_patch,
)
from app.services.source_manifest import build_source_manifest, save_source_manifest

logger = logging.getLogger(__name__)


def _skip_live_refine() -> bool:
    return os.environ.get("SKIP_LIVE_REFINE", "").strip() in ("1", "true", "yes")


def _refine_timing_meta(result: RefineAllPagesResult) -> dict[str, Any]:
    timing = result.refine_ms_by_page or {}
    if not timing:
        return {}
    total = sum(timing.values())
    n = len(timing)
    return {
        "refine_timing": {
            "by_page": {f"page_{k}": v for k, v in sorted(timing.items())},
            "total_ms": total,
            "avg_ms": int(total / n) if n else 0,
            "pages": n,
        }
    }


def _finalize_page_blocks(
    page_blocks: list[DocumentBlock],
    *,
    profile: dict[str, Any],
    page_w: float,
    page_h: float,
    page_result=None,
) -> list[DocumentBlock]:
    """Apply refine (optional) -> math enrich -> slim layout enrich for one page.

    Math runs before slim because slim's reading-order/zones do not depend on the
    text, and we want the streamed HTML to already be the final, enriched form.
    """
    if page_result is not None:
        page_blocks = apply_page_refine(page_blocks, page_result)
    page_blocks = enrich_page_blocks_math(page_blocks)
    page_blocks = enrich_page_blocks_slim(
        page_blocks, profile=profile, page_w=page_w, page_h=page_h
    )
    return page_blocks


def _refine_one_page_with_retry(
    pdf_bytes: bytes,
    page_no: int,
    page_blocks: list[DocumentBlock],
    *,
    profile_id: str,
    mode: str,
    render_cache: PdfRenderCache,
) -> tuple[Any, int, Exception | None]:
    """Return (PageRefineResult|None, elapsed_ms, last_error)."""
    last_err: Exception | None = None
    for attempt in range(2):
        t0 = time.perf_counter()
        try:
            result = refine_page(
                pdf_bytes,
                page_no,
                page_blocks,
                profile_id=profile_id,
                mode=mode,
                render_cache=render_cache,
            )
            return result, int((time.perf_counter() - t0) * 1000), None
        except Exception as exc:
            last_err = exc
            logger.warning("refine page %d attempt %d: %s", page_no, attempt + 1, exc)
    return None, 0, last_err


def _run_refine_and_finalize_streaming(
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
) -> tuple[list[DocumentBlock], RefineAllPagesResult]:
    """Per-page final pipeline: refine -> math -> slim -> render -> stream.

    Every target page is enriched and rendered regardless of whether 35B refine
    succeeds, so the streamed HTML is already the final layout. Pages outside
    ``only_pages`` are left untouched (partial rebuild).

    When ``debug_job`` is set and ``settings.corpus_debug_dump``, also writes
    per-page debug/T1 under ``corpus/imports/.../mineru/debug/``.
    ``raw_blocks`` (Cloud content_list) is sliced into ``mineru_page.json``.
    """
    from app.config import settings
    from app.services.pipeline_telemetry import ImportCanceled, raise_if_canceled

    mode = get_refine_mode(profile_id)
    do_refine = mode != "skip" and not _skip_live_refine()
    profile = load_textbook_profile(profile_id)
    result = RefineAllPagesResult(prompt_version=prompt_version_for_mode(mode))

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

    # When 35B is skipped, progress still walks pages for enrich+HTML — label as render.
    progress_phase = PIPELINE_PHASE_REFINE if do_refine else PIPELINE_PHASE_RENDER

    if progress_cb:
        try:
            progress_cb(pipeline_patch(progress_phase, refine_total=total))
        except Exception:
            pass

    parallel = get_refine_parallel(profile_id) if do_refine else 1

    # Producer/consumer: only the vLLM refine call is parallelized. All block
    # mutation, enrichment, rendering and DB writes happen on this thread.
    refined_map: dict[int, tuple[Any, int, Exception | None]] = {}
    if do_refine and parallel > 1 and target_pages:
        if job_id:
            raise_if_canceled(job_id, verify_corpus_job=True)
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = {
                pool.submit(
                    _refine_one_page_with_retry,
                    pdf_bytes,
                    page_no,
                    by_page[page_no],
                    profile_id=profile_id,
                    mode=mode,
                    render_cache=render_cache,
                ): page_no
                for page_no in target_pages
            }
            for fut in futures:
                page_no = futures[fut]
                try:
                    refined_map[page_no] = fut.result()
                except Exception as exc:  # pragma: no cover - defensive
                    refined_map[page_no] = (None, 0, exc)

    try:
        for idx, page_no in enumerate(target_pages, start=1):
            if job_id:
                # Redis every page; DB row check every 5 pages (cancel flag is primary).
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

            page_blocks = by_page[page_no]
            page_result = None
            if do_refine:
                if page_no in refined_map:
                    page_result, refine_ms, last_err = refined_map[page_no]
                else:
                    page_result, refine_ms, last_err = _refine_one_page_with_retry(
                        pdf_bytes,
                        page_no,
                        page_blocks,
                        profile_id=profile_id,
                        mode=mode,
                        render_cache=render_cache,
                    )
                if page_result is not None:
                    result.pages.append(page_result)
                    result.refine_ms_by_page[page_no] = refine_ms
                else:
                    result.errors.append(
                        {
                            "page_no": page_no,
                            "error": str(last_err)[:500] if last_err else "unknown",
                        }
                    )

            pw, ph = render_cache.page_size(page_no)
            page_blocks = _finalize_page_blocks(
                page_blocks,
                profile=profile,
                page_w=pw,
                page_h=ph,
                page_result=page_result,
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
                if page_html_cb is not None:
                    try:
                        page_html_cb(page_no, html)
                    except ImportCanceled:
                        raise
                    except Exception as e:
                        msg = str(e).lower()
                        # Job row deleted mid-run → stop the page loop, don't keep rendering.
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

    out = [b for page_no in all_pages for b in by_page[page_no]]
    out.sort(key=lambda b: (b.page_no, b.order))
    return out, result


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
    """Convert parsed output to DocumentBlock IR and run optional refine.

    If ``page_html_cb(page_no, html)`` is provided it is called after each
    page's finalize completes so callers can persist pages incrementally.

    ``expected_pages`` is the absolute page span this segment should cover;
    gaps get blank placeholders and content-dropped pages are reported via
    ``get_last_missing_pages()``.
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

        # Single per-page pipeline: refine -> math -> slim -> render -> stream.
        # The streamed HTML is already final, so persist does not re-render.
        blocks, refine = _run_refine_and_finalize_streaming(
            blocks,
            pdf_bytes=pdf_bytes,
            profile_id=pid,
            progress_cb=progress_cb,
            page_html_cb=page_html_cb,
            job_id=job_id,
            debug_job=debug_job,
            raw_blocks=raw_blocks if isinstance(raw_blocks, list) else None,
        )
        _last_refine_meta = {
            "refine_prompt_version": refine.prompt_version or REFINE_PROMPT_VERSION,
            "page_refine_chunks": refine_result_to_meta_chunks(refine),
            "refine_errors": refine.errors,
            **_refine_timing_meta(refine),
        }
        return blocks

    # ``vision_pages`` path removed together with the vision_ocr engine — the
    # MinerU line always produces ``raw_blocks``.
    return []


# Module-level refine meta written by build_ir_from_parse
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
    persists blocks.json, the source manifest, and job meta.
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
    # Freeze per-page MinerU IR once so Studio can roll back after 35B edits.
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

    refine_meta = get_last_refine_meta()
    meta["blocks_key"] = blocks_key
    meta["block_count"] = len(blocks)
    meta["has_structure"] = True
    meta["content_format"] = "html"
    meta["source_manifest_key"] = manifest_key
    meta["textbook_profile"] = profile_id
    meta["refine_done_at"] = _now_iso_safe()
    meta.update(refine_meta)
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
        "import IR persisted job=%s blocks=%d pages=%d format=html",
        job.id,
        len(blocks),
        total,
    )


def rerender_page_html(
    blocks: list[DocumentBlock],
    page_no: int,
    *,
    profile_id: str | None = None,
) -> str:
    return render_page_html(blocks, page_no, profile_id=profile_id or default_profile_id())


def get_job_ir(job: CorpusImportJob) -> list[DocumentBlock] | None:
    return load_blocks(job)


def rebuild_job_ir(
    session,
    job: CorpusImportJob,
    *,
    progress_cb=None,
    pages: list[int] | None = None,
) -> tuple[list[DocumentBlock], dict[str, Any]]:
    """Re-run 35B refine + layout on stored blocks (no MinerU re-parse).

    ``pages=None`` rebuilds every page; ``pages=[12, 16]`` only re-refines,
    re-enriches and re-renders those pages while leaving the rest untouched.
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

    blocks, refine = _run_refine_and_finalize_streaming(
        blocks,
        pdf_bytes=pdf_bytes,
        profile_id=profile_id,
        progress_cb=progress_cb,
        page_html_cb=_page_html_cb,
        only_pages=only_pages,
    )

    blocks_key = save_blocks(job, blocks)

    refine_meta = {
        "refine_prompt_version": refine.prompt_version or REFINE_PROMPT_VERSION,
        "page_refine_chunks": refine_result_to_meta_chunks(refine),
        "refine_errors": refine.errors,
        **_refine_timing_meta(refine),
    }
    meta["blocks_key"] = blocks_key
    meta["block_count"] = len(blocks)
    meta["has_structure"] = True
    meta["content_format"] = "html"
    meta["textbook_profile"] = profile_id
    meta["refine_done_at"] = _now_iso_safe()
    if only_pages:
        meta["rebuilt_pages"] = sorted(only_pages)
    meta.update(refine_meta)
    _set_job_meta(session, job, meta)
    writer.maybe_commit(force=True)
    logger.info(
        "import IR rebuilt job=%s blocks=%d pages=%s refine_chunks=%d errors=%d",
        job.id,
        len(blocks),
        "all" if only_pages is None else sorted(only_pages),
        len(refine_meta.get("page_refine_chunks") or []),
        len(refine_meta.get("refine_errors") or []),
    )
    return blocks, refine_meta


def _now_iso_safe() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
