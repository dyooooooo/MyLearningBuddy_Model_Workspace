"""Studio-facing URLs for import assets (no MinIO hostname in stored markdown)."""
from __future__ import annotations

from urllib.parse import quote

# Must match studio proxy: /proxy/{path} → /api/v1/admin/corpus/{path}
# Do NOT prefix with "corpus/" — that would double the segment.
STUDIO_IMPORT_ASSET_PATH = "/api/v1/studio/proxy/import/asset"


def import_asset_studio_url(minio_key: str) -> str:
    return f"{STUDIO_IMPORT_ASSET_PATH}?key={quote(minio_key, safe='')}"
