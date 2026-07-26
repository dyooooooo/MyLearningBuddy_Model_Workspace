"""Persist / load document IR blocks in MinIO."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.clients.minio_client import get_object_bytes, put_import_bytes
from app.db.models import CorpusImportJob
from app.services.document_ir import DocumentBlock, document_block_to_dict, document_blocks_from_dicts
from app.services.import_assets import asset_prefix_from_minio_key, assert_allowed_asset_key
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

BLOCKS_FILENAME = "blocks.json"


def blocks_minio_key(job: CorpusImportJob) -> str:
    if not job.minio_key:
        raise ValueError("import job has no minio_key")
    prefix = asset_prefix_from_minio_key(job.minio_key)
    return f"{prefix}/{BLOCKS_FILENAME}"


def save_blocks(job: CorpusImportJob, blocks: list[DocumentBlock]) -> str:
    key = blocks_minio_key(job)
    assert_allowed_asset_key(key)
    payload = json.dumps(
        [document_block_to_dict(b) for b in blocks],
        ensure_ascii=False,
    ).encode("utf-8")
    put_import_bytes(key, payload, content_type="application/json")
    logger.info("import IR saved job=%s key=%s blocks=%d", job.id, key, len(blocks))
    return key


def load_blocks(job: CorpusImportJob) -> list[DocumentBlock] | None:
    meta = loads(job.meta_json, {})
    key = meta.get("blocks_key")
    if not key:
        return None
    try:
        assert_allowed_asset_key(key)
    except ValueError:
        return None
    raw = get_object_bytes(key)
    if not raw:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, list):
            return document_blocks_from_dicts(parsed)
    except Exception as exc:
        logger.warning("import IR load failed job=%s key=%s: %s", job.id, key, exc)
    return None


def patch_job_ir_meta(job: CorpusImportJob, blocks: list[DocumentBlock], blocks_key: str) -> dict[str, Any]:
    meta = loads(job.meta_json, {})
    meta["blocks_key"] = blocks_key
    meta["block_count"] = len(blocks)
    meta["has_structure"] = True
    job.meta_json = dumps(meta)
    return meta
