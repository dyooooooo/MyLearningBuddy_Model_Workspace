"""Create corpus feedback rows + optional screenshot upload (MinerU line)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.clients.minio_client import put_import_bytes
from app.db.models import CorpusImportFeedback, CorpusImportJob
from app.services import corpus_feedback_store as store
from app.services.corpus_debug_bundle import (
    PAGE_IMAGE_FILE,
    debug_page_base,
    load_t1_snapshot,
)
from app.services.import_assets import (
    assert_allowed_asset_key,
    asset_prefix_from_minio_key,
)

logger = logging.getLogger(__name__)

_MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024


def _feedback_screenshot_key(asset_prefix: str, feedback_id: str) -> str:
    return f"{asset_prefix.rstrip('/')}/mineru/feedback/{feedback_id}/screenshot.jpg"


def create_page_feedback(
    session: Session,
    *,
    job: CorpusImportJob,
    page_no: int,
    body: str,
    tags: list[str] | None = None,
    block_id: str = "",
    screenshot_bytes: bytes | None = None,
    screenshot_content_type: str = "image/jpeg",
) -> CorpusImportFeedback:
    asset_prefix = asset_prefix_from_minio_key(job.minio_key or "")
    debug_prefix = debug_page_base(job, page_no)
    input_page_key = f"{debug_prefix}/{PAGE_IMAGE_FILE}"
    t1_snapshot = load_t1_snapshot(job, page_no)

    row = store.create_feedback(
        session,
        job_id=job.id,
        page_no=page_no,
        label=job.label or "",
        body=body.strip(),
        tags=tags,
        block_id=block_id,
        input_page_key=input_page_key,
        t1_snapshot=t1_snapshot,
        debug_prefix=debug_prefix,
    )

    if screenshot_bytes:
        if len(screenshot_bytes) > _MAX_SCREENSHOT_BYTES:
            raise ValueError(f"screenshot too large (max {_MAX_SCREENSHOT_BYTES} bytes)")
        key = _feedback_screenshot_key(asset_prefix, row.id)
        assert_allowed_asset_key(key)
        ct = (
            screenshot_content_type
            if screenshot_content_type.startswith("image/")
            else "image/jpeg"
        )
        put_import_bytes(key, screenshot_bytes, content_type=ct)
        row.screenshot_key = key
        session.flush()

    return row


def patch_feedback(
    session: Session,
    row: CorpusImportFeedback,
    *,
    status: str | None = None,
    resolution_note: str | None = None,
) -> CorpusImportFeedback:
    store.set_feedback_status(
        session,
        row,
        status=status,
        resolution_note=resolution_note,
    )
    return row
