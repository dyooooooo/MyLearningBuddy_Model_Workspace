"""Isolated MinIO asset helpers for the 35B import line.

Copied from the shared ``import_assets`` module so the 35B pipeline does not
import any MinerU-side service code. The only shared low-level dependency is
``minio_client`` (object storage), analogous to the DB session — 35B and MinerU
live under different key prefixes in the same bucket.

Figure image URLs embedded into 35B HTML point at the 35B-owned asset route so
the self-contained preview page never depends on the corpus/Studio proxy.
"""
from __future__ import annotations

import logging
from pathlib import PurePosixPath
from urllib.parse import quote

from app.clients.minio_client import put_import_bytes

logger = logging.getLogger(__name__)

# FULL MinIO isolation: the 35B line owns ONLY the ``import35b/`` top-level
# prefix. It must never read/write MinerU's ``corpus/imports/`` objects.
IMPORT35B_PREFIX = "import35b/"
_ALLOWED_PREFIXES = (IMPORT35B_PREFIX,)

# 35B-owned asset serving path (see app/routes/import35b.py). Same origin as the
# self-contained preview page — the 35B line does NOT go through the Studio proxy.
IMPORT35B_ASSET_PATH = "/api/v1/admin/import35b/asset"


def assert_allowed_asset_key(key: str) -> None:
    normalized = key.lstrip("/")
    if not any(normalized.startswith(p) for p in _ALLOWED_PREFIXES):
        raise ValueError(f"asset key not allowed (35B line requires import35b/ prefix): {key}")


def import35b_pdf_key(job_id: str) -> str:
    """Canonical MinIO key for a 35B source PDF, under the isolated prefix."""
    return f"{IMPORT35B_PREFIX}{job_id}/source.pdf"


def job_minio_prefixes(minio_key: str, job_id: str) -> list[str]:
    """All MinIO directory prefixes that may hold this job's PDF + assets + debug dumps."""
    prefixes: set[str] = {f"{IMPORT35B_PREFIX}{job_id}/"}
    normalized = (minio_key or "").lstrip("/")
    if normalized.startswith(IMPORT35B_PREFIX):
        parent = str(PurePosixPath(normalized).parent)
        if parent.startswith(IMPORT35B_PREFIX.rstrip("/")):
            prefixes.add(f"{parent.rstrip('/')}/")
    return sorted(prefixes)


def asset_prefix_from_minio_key(minio_key: str) -> str:
    parent = str(PurePosixPath(minio_key).parent)
    return f"{parent}/assets"


def import35b_asset_url(minio_key: str) -> str:
    return f"{IMPORT35B_ASSET_PATH}?key={quote(minio_key, safe='')}"


def upload_import_asset(key: str, data: bytes, *, content_type: str) -> str:
    """Upload bytes to MinIO; return the 35B asset URL for HTML embedding."""
    assert_allowed_asset_key(key)
    put_import_bytes(key, data, content_type=content_type)
    return import35b_asset_url(key)
