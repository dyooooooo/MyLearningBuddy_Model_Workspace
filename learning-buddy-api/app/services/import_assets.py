"""Import corpus assets (figures, page scans) — MinIO storage + Studio proxy URLs."""
from __future__ import annotations

import logging
from pathlib import PurePosixPath
from urllib.parse import quote

from app.clients.minio_client import put_import_bytes
from app.services.import_assets_urls import import_asset_studio_url

logger = logging.getLogger(__name__)

_ALLOWED_PREFIXES = ("corpus/imports/", "corpus/import/")


def assert_allowed_asset_key(key: str) -> None:
    normalized = key.lstrip("/")
    if not any(normalized.startswith(p) for p in _ALLOWED_PREFIXES):
        raise ValueError(f"asset key not allowed: {key}")


def asset_prefix_from_minio_key(minio_key: str) -> str:
    """Derive shared asset folder from uploaded PDF minio key."""
    parent = str(PurePosixPath(minio_key).parent)
    return f"{parent}/assets"


def upload_import_asset(key: str, data: bytes, *, content_type: str) -> str:
    """Upload bytes to MinIO; return Studio-proxy URL for markdown embedding."""
    assert_allowed_asset_key(key)
    put_import_bytes(key, data, content_type=content_type)
    return import_asset_studio_url(key)


def rewrite_local_image_refs(text: str, url_map: dict[str, str]) -> str:
    """Replace MinerU relative image paths only (images/foo.png), never bare filenames."""
    if not text or not url_map:
        return text
    out = text
    locals_only = [k for k in url_map if k.startswith("images/")]
    for local in sorted(locals_only, key=len, reverse=True):
        out = out.replace(local, url_map[local])
    return out
