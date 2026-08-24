"""MinIO helpers for the knowledge-base prefix only."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote

from app.clients.minio_client import get_object_bytes, put_import_bytes

KB_PREFIX = "kb/pdfs"
KB_ASSET_PATH = "/api/v1/kb/proxy/asset"


def assert_kb_key(key: str) -> str:
    normalized = (key or "").lstrip("/")
    if not normalized.startswith(f"{KB_PREFIX}/"):
        raise ValueError(f"key must start with {KB_PREFIX}/")
    return normalized


def book_prefix(book_id: str) -> str:
    return f"{KB_PREFIX}/{book_id}"


def asset_url(minio_key: str) -> str:
    return f"{KB_ASSET_PATH}?key={quote(assert_kb_key(minio_key), safe='')}"


def upload_bytes(key: str, data: bytes, *, content_type: str) -> str:
    normalized = assert_kb_key(key)
    put_import_bytes(normalized, data, content_type=content_type)
    return asset_url(normalized)


def read_bytes(key: str) -> bytes | None:
    return get_object_bytes(assert_kb_key(key))


def content_type_for_name(name: str) -> str:
    ext = PurePosixPath(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
        ".json": "application/json",
    }.get(ext, "application/octet-stream")


def rewrite_local_image_paths(text: str, url_map: dict[str, str]) -> str:
    if not text or not url_map:
        return text
    out = text
    locals_only = [k for k in url_map if k.startswith("images/")]
    for local in sorted(locals_only, key=len, reverse=True):
        out = out.replace(local, url_map[local])
    return out
