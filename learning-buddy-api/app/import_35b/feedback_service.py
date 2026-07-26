"""Create user feedback rows and upload optional screenshots to MinIO."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.clients.minio_client import get_object_bytes, put_import_bytes
from app.db.models import Import35bFeedback, Import35bJob
from app.import_35b import store
from app.import_35b.assets import asset_prefix_from_minio_key, assert_allowed_asset_key
from app.import_35b.debug_bundle import debug_page_base, PAGE_IMAGE_FILE
from app.import_35b.vision_layout import PROMPT_VERSION

logger = logging.getLogger(__name__)

_MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024


def _feedback_screenshot_key(asset_prefix: str, feedback_id: str) -> str:
    return f"{asset_prefix.rstrip('/')}/35b/feedback/{feedback_id}/screenshot.jpg"


def _load_t1_snapshot(job: Import35bJob, page_no: int) -> list[dict[str, Any]]:
    base = debug_page_base(job, page_no)
    data = get_object_bytes(f"{base}/debug.json")
    if not data:
        return []
    try:
        meta = json.loads(data)
    except json.JSONDecodeError:
        return []
    findings = meta.get("findings")
    return findings if isinstance(findings, list) else []


def create_page_feedback(
    session: Session,
    *,
    job: Import35bJob,
    page_no: int,
    body: str,
    tags: list[str] | None = None,
    block_id: str = "",
    screenshot_bytes: bytes | None = None,
    screenshot_content_type: str = "image/jpeg",
) -> Import35bFeedback:
    asset_prefix = asset_prefix_from_minio_key(job.minio_key)
    debug_prefix = debug_page_base(job, page_no)
    input_page_key = f"{debug_prefix}/{PAGE_IMAGE_FILE}"
    t1_snapshot = _load_t1_snapshot(job, page_no)

    row = store.create_feedback(
        session,
        job_id=job.id,
        page_no=page_no,
        textbook_key=job.textbook_key,
        label=job.label,
        body=body.strip(),
        tags=tags,
        block_id=block_id,
        input_page_key=input_page_key,
        t1_snapshot=t1_snapshot,
        debug_prefix=debug_prefix,
        prompt_version=PROMPT_VERSION,
    )

    if screenshot_bytes:
        if len(screenshot_bytes) > _MAX_SCREENSHOT_BYTES:
            raise ValueError(f"screenshot too large (max {_MAX_SCREENSHOT_BYTES} bytes)")
        key = _feedback_screenshot_key(asset_prefix, row.id)
        assert_allowed_asset_key(key)
        ct = screenshot_content_type if screenshot_content_type.startswith("image/") else "image/jpeg"
        put_import_bytes(key, screenshot_bytes, content_type=ct)
        row.screenshot_key = key
        session.flush()

    return row


def patch_feedback(
    session: Session,
    row: Import35bFeedback,
    *,
    status: str | None = None,
    resolution_note: str | None = None,
) -> Import35bFeedback:
    store.set_feedback_status(
        session,
        row,
        status=status,
        resolution_note=resolution_note,
    )
    return row
