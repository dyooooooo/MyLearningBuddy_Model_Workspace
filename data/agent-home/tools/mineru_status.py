from __future__ import annotations

import json
from typing import Any


def mineru_status(book_id: str) -> dict[str, Any]:
    """Read local textbooks/<id>/meta.json and mineru manifest if present."""
    from app import library
    from app.config import settings

    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    meta = library.read_meta(sid)
    if not meta:
        return {"book_id": sid, "ok": False, "reason": "meta_missing"}
    folder = settings.textbooks_dir / sid
    mineru = folder / "mineru" / "manifest.json"
    manifest: dict[str, Any] = {}
    if mineru.is_file():
        try:
            data = json.loads(mineru.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                manifest = data
        except (OSError, json.JSONDecodeError):
            manifest = {}
    return {
        "book_id": sid,
        "ok": True,
        "status": str(meta.get("status") or ""),
        "label": str(meta.get("label") or ""),
        "subject": str(meta.get("subject") or ""),
        "category": str(meta.get("category") or ""),
        "page_count": int(meta.get("page_count") or 0),
        "error": str(meta.get("error") or ""),
        "mineru": {
            "present": bool(manifest),
            "block_count": manifest.get("block_count"),
            "page_ranges": manifest.get("page_ranges") or "",
            "files": manifest.get("files") or [],
        },
    }
