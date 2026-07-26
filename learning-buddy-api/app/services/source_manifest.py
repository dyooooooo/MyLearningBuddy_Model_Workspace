"""Build source_manifest.json for import jobs (PDF + images index)."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.clients.minio_client import put_import_bytes
from app.db.models import CorpusImportJob
from app.services.document_ir import DocumentBlock
from app.services.import_assets import asset_prefix_from_minio_key, assert_allowed_asset_key
from app.services.ir.textbook_profile import default_profile_id

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "source_manifest.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_minio_key(job: CorpusImportJob) -> str:
    prefix = asset_prefix_from_minio_key(job.minio_key)
    return f"{prefix}/{MANIFEST_FILENAME}"


def _image_kind(block: DocumentBlock) -> str:
    meta = block.meta or {}
    fk = meta.get("figure_kind")
    if fk == "page_scan":
        return "page_scan"
    if fk == "embedded":
        return "embedded"
    engine = meta.get("engine", "")
    if "mineru" in str(engine):
        return "mineru_crop"
    return "embedded"


def build_source_manifest(
    job: CorpusImportJob,
    blocks: list[DocumentBlock],
    *,
    pdf_bytes: bytes,
    blocks_key: str,
    parse_engine: str,
    profile_id: str | None = None,
) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for block in blocks:
        key = block.asset_key
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        images.append(
            {
                "key": key,
                "page_no": block.page_no,
                "block_id": block.id,
                "kind": _image_kind(block),
                "engine": str((block.meta or {}).get("engine") or parse_engine),
            }
        )
    return {
        "pdf_key": job.minio_key,
        "pdf_sha256": _sha256(pdf_bytes) if pdf_bytes else "",
        "page_count": len({b.page_no for b in blocks}),
        "parse_engine": parse_engine,
        "blocks_key": blocks_key,
        "profile_id": profile_id or default_profile_id(),
        "images": images,
    }


def save_source_manifest(job: CorpusImportJob, manifest: dict[str, Any]) -> str:
    key = manifest_minio_key(job)
    assert_allowed_asset_key(key)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    put_import_bytes(key, payload, content_type="application/json")
    logger.info("source manifest saved job=%s key=%s images=%d", job.id, key, len(manifest.get("images") or []))
    return key
