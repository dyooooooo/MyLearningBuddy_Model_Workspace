"""Page reprocess + single-block refine for the 35B import line."""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes
from app.config import settings
from app.db.session import get_session_factory
from app.import_35b import store
from app.import_35b.assets import asset_prefix_from_minio_key
from app.import_35b.debug_bundle import PAGE_IMAGE_FILE, debug_page_base
from app.import_35b.mineru_layout import load_page_hints
from app.import_35b.page_pipeline import compile_from_blueprint, process_page
from app.import_35b.profile_features import merge_runtime_profile
from app.import_35b.raster import DEFAULT_DPI, PdfRasterCache
from app.import_35b.render_zone import html_to_plain_text
from app.import_35b.review_service import _load_page_blueprint_dict
from app.import_35b.schemas import PageBlueprint
from app.import_35b.vision_layout import refine_single_block
from app.util.json_util import loads

logger = logging.getLogger(__name__)

_ALLOWED_STATUSES = frozenset({"done", "failed", "converting"})
_ReprocessMode = Literal["full", "from_raw"]

_page_locks: dict[tuple[str, int], threading.Lock] = {}
_page_locks_guard = threading.Lock()


def _page_lock(job_id: str, page_no: int) -> threading.Lock:
    key = (job_id, int(page_no))
    with _page_locks_guard:
        lock = _page_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _page_locks[key] = lock
        return lock


def _mark_feedback_fixed(session: Session, feedback_id: str | None, note: str) -> None:
    if not feedback_id:
        return
    row = store.get_feedback(session, feedback_id)
    if row is None:
        logger.warning("reprocess feedback not found id=%s", feedback_id)
        return
    store.set_feedback_status(
        session,
        row,
        status="fixed",
        resolution_note=note,
    )


def _load_raw_model(asset_prefix: str, page_no: int) -> dict[str, Any]:
    key = f"{asset_prefix.rstrip('/')}/35b/debug/p{page_no:04d}/raw_model.json"
    data = get_object_bytes(key)
    if not data:
        raise ValueError(f"raw_model.json not found for page {page_no}")
    try:
        obj = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"raw_model.json invalid for page {page_no}: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(f"raw_model.json must be an object for page {page_no}")
    return obj


def _load_page_jpeg(job, page_no: int, pdf_bytes: bytes) -> bytes:
    base = debug_page_base(job, page_no)
    jpeg = get_object_bytes(f"{base}/{PAGE_IMAGE_FILE}")
    if jpeg:
        return jpeg
    with PdfRasterCache(pdf_bytes) as raster:
        return raster.page_jpeg(page_no, dpi=DEFAULT_DPI)


def reprocess_page(
    job_id: str,
    page_no: int,
    *,
    mode: _ReprocessMode = "full",
    reason: str = "",
    feedback_id: str | None = None,
) -> dict[str, Any]:
    """Re-run one page (full VLM or from saved raw_model) and upsert HTML/blueprint."""
    if mode not in ("full", "from_raw"):
        raise ValueError(f"invalid mode: {mode}")
    page_no = int(page_no)
    lock = _page_lock(job_id, page_no)
    if not lock.acquire(blocking=False):
        raise RuntimeError(f"page {page_no} is already being reprocessed")

    factory = get_session_factory()
    try:
        with factory() as session:
            job = store.get_job(session, job_id)
            if job is None:
                raise ValueError("job not found")
            if job.status not in _ALLOWED_STATUSES:
                raise ValueError(
                    f"job status must be one of {sorted(_ALLOWED_STATUSES)}, got {job.status}"
                )
            textbook_key = job.textbook_key
            minio_key = job.minio_key
            profile_id = job.profile_id or "hujiao_math_hs_v1"
            meta = loads(getattr(job, "meta_json", None) or "{}", {})

        pdf_bytes = get_object_bytes(minio_key)
        if not pdf_bytes:
            raise ValueError(f"PDF not found in storage: {minio_key}")

        asset_prefix = asset_prefix_from_minio_key(minio_key)
        layout_hints = load_page_hints(asset_prefix, page_no)
        layout_stats = meta.get("layout_stats") if isinstance(meta.get("layout_stats"), dict) else None
        profile = merge_runtime_profile(profile_id, layout_stats)

        from_raw: dict[str, Any] | None = None
        if mode == "from_raw":
            from_raw = _load_raw_model(asset_prefix, page_no)

        with PdfRasterCache(pdf_bytes) as raster:
            result = process_page(
                page_no=page_no,
                pdf_bytes=pdf_bytes,
                raster=raster,
                asset_prefix=asset_prefix,
                profile_id=profile_id,
                layout_hints=layout_hints,
                profile=profile,
                from_raw=from_raw,
                debug_on=settings.import35b_debug_dump,
            )

        with factory() as session:
            job = store.get_job(session, job_id)
            if job is None:
                raise ValueError("job not found")
            store.upsert_page(
                session,
                textbook_key=textbook_key,
                job_id=job_id,
                page_no=page_no,
                html=result.html,
                plain_text=html_to_plain_text(result.html),
            )
            note = f"reprocess mode={mode} reason={reason or 'user_request'}"
            _mark_feedback_fixed(session, feedback_id, note)
            session.commit()

        logger.info(
            "import35b reprocess ok job=%s page=%d mode=%s status=%s findings=%d",
            job_id,
            page_no,
            mode,
            result.status,
            len(result.findings),
        )
        return {
            "ok": True,
            "job_id": job_id,
            "page_no": page_no,
            "mode": mode,
            "status": result.status,
            "findings_count": len(result.findings),
            "findings": result.findings,
            "quality": result.quality,
            "elapsed_ms": result.elapsed_ms,
            "reason": reason or "user_request",
            "feedback_id": feedback_id,
            "blocks": len(result.blueprint.blocks) if result.blueprint else 0,
        }
    finally:
        lock.release()


def refine_block(
    job_id: str,
    page_no: int,
    block_id: str,
    *,
    note: str = "",
    feedback_id: str | None = None,
) -> dict[str, Any]:
    """Vision-refine one block, recompile the page, upsert HTML/blueprint."""
    page_no = int(page_no)
    block_id = str(block_id or "").strip()
    if not block_id:
        raise ValueError("block_id required")

    lock = _page_lock(job_id, page_no)
    if not lock.acquire(blocking=False):
        raise RuntimeError(f"page {page_no} is already being reprocessed")

    factory = get_session_factory()
    try:
        with factory() as session:
            job = store.get_job(session, job_id)
            if job is None:
                raise ValueError("job not found")
            if job.status not in _ALLOWED_STATUSES:
                raise ValueError(
                    f"job status must be one of {sorted(_ALLOWED_STATUSES)}, got {job.status}"
                )
            textbook_key = job.textbook_key
            minio_key = job.minio_key
            profile_id = job.profile_id or "hujiao_math_hs_v1"
            bp_dict = _load_page_blueprint_dict(job, page_no)
            if bp_dict is None:
                raise ValueError(f"blueprint for page {page_no} not found")

        blocks = bp_dict.get("blocks") or []
        target: dict[str, Any] | None = None
        target_idx = -1
        for i, b in enumerate(blocks):
            if isinstance(b, dict) and str(b.get("id") or "") == block_id:
                target = b
                target_idx = i
                break
        if target is None:
            raise ValueError(f"block {block_id} not found on page {page_no}")

        pdf_bytes = get_object_bytes(minio_key)
        if not pdf_bytes:
            raise ValueError(f"PDF not found in storage: {minio_key}")

        asset_prefix = asset_prefix_from_minio_key(minio_key)
        hints = load_page_hints(asset_prefix, page_no) or []
        layout_hint = next(
            (h for h in hints if isinstance(h, dict) and str(h.get("id") or "") == block_id),
            hints[0] if hints else None,
        )

        with factory() as session:
            job = store.get_job(session, job_id)
            if job is None:
                raise ValueError("job not found")
            page_jpeg = _load_page_jpeg(job, page_no, pdf_bytes)

        refined = refine_single_block(
            page_jpeg=page_jpeg,
            page_no=page_no,
            block=target,
            note=note,
            layout_hint=layout_hint,
        )
        blocks[target_idx] = refined
        bp_dict["blocks"] = blocks
        bp = PageBlueprint.model_validate(bp_dict)

        result = compile_from_blueprint(
            bp,
            pdf_bytes=pdf_bytes,
            asset_prefix=asset_prefix,
            profile_id=profile_id,
            page_no=page_no,
            debug_on=settings.import35b_debug_dump,
        )

        with factory() as session:
            job = store.get_job(session, job_id)
            if job is None:
                raise ValueError("job not found")
            store.upsert_page(
                session,
                textbook_key=textbook_key,
                job_id=job_id,
                page_no=page_no,
                html=result.html,
                plain_text=html_to_plain_text(result.html),
            )
            _mark_feedback_fixed(
                session,
                feedback_id,
                f"refine_block block_id={block_id} note={note or ''}",
            )
            session.commit()

        logger.info(
            "import35b refine_block ok job=%s page=%d block=%s findings=%d",
            job_id,
            page_no,
            block_id,
            len(result.findings),
        )
        return {
            "ok": True,
            "job_id": job_id,
            "page_no": page_no,
            "block_id": block_id,
            "status": result.status,
            "findings_count": len(result.findings),
            "findings": result.findings,
            "quality": result.quality,
            "elapsed_ms": result.elapsed_ms,
            "feedback_id": feedback_id,
        }
    finally:
        lock.release()
