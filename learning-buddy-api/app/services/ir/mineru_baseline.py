"""Durable MinerU page baseline IR (rollback point for 35B edits).

Baseline lives under ``{asset_prefix}/mineru/baseline/p{NNNN}/blocks.json``.
It is written once (lazy or at first MinerU restore) and **never** overwritten by
35B refine / layout_rebuild. Only an explicit MinerU refresh may replace it.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.clients.minio_client import get_object_bytes, put_import_bytes
from app.db.models import CorpusImportJob
from app.services.document_ir import (
    DocumentBlock,
    document_block_to_dict,
    document_blocks_from_dicts,
)
from app.services.import_assets import asset_prefix_from_minio_key, assert_allowed_asset_key

logger = logging.getLogger(__name__)


def baseline_page_key(job: CorpusImportJob, page_no: int) -> str:
    prefix = asset_prefix_from_minio_key(job.minio_key or "").rstrip("/")
    return f"{prefix}/mineru/baseline/p{page_no:04d}/blocks.json"


def load_mineru_baseline(
    job: CorpusImportJob, page_no: int
) -> list[DocumentBlock] | None:
    if not job.minio_key:
        return None
    key = baseline_page_key(job, page_no)
    try:
        assert_allowed_asset_key(key)
    except ValueError:
        return None
    raw = get_object_bytes(key)
    if not raw:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        logger.warning("mineru baseline load failed job=%s page=%s: %s", job.id, page_no, exc)
        return None
    if not isinstance(parsed, list):
        return None
    blocks = document_blocks_from_dicts(parsed)
    return [b for b in blocks if b.page_no == page_no]


def save_mineru_baseline(
    job: CorpusImportJob,
    page_no: int,
    page_blocks: list[DocumentBlock],
    *,
    overwrite: bool = False,
) -> str | None:
    """Persist MinerU baseline for one page.

    Default ``overwrite=False``: if a baseline already exists, leave it untouched
    and return its key (protects rollback snapshot).
    """
    if not job.minio_key:
        return None
    key = baseline_page_key(job, page_no)
    assert_allowed_asset_key(key)
    if not overwrite:
        existing = get_object_bytes(key)
        if existing:
            logger.info(
                "mineru baseline keep existing job=%s page=%s key=%s",
                job.id,
                page_no,
                key,
            )
            return key
    only = [b for b in page_blocks if b.page_no == page_no]
    payload = json.dumps(
        [document_block_to_dict(b) for b in only],
        ensure_ascii=False,
    ).encode("utf-8")
    put_import_bytes(key, payload, content_type="application/json")
    logger.info(
        "mineru baseline saved job=%s page=%s blocks=%d overwrite=%s",
        job.id,
        page_no,
        len(only),
        overwrite,
    )
    return key


def ensure_mineru_baseline(
    job: CorpusImportJob,
    page_no: int,
    page_blocks: list[DocumentBlock],
) -> bool:
    """If no baseline yet, snapshot current page blocks. Returns True if wrote."""
    if load_mineru_baseline(job, page_no) is not None:
        return False
    if not page_blocks:
        return False
    save_mineru_baseline(job, page_no, page_blocks, overwrite=False)
    return True


def baseline_meta_stub(job: CorpusImportJob, page_no: int) -> dict[str, Any]:
    exists = load_mineru_baseline(job, page_no) is not None
    return {
        "mineru_baseline_key": baseline_page_key(job, page_no) if job.minio_key else "",
        "mineru_baseline_exists": exists,
    }
