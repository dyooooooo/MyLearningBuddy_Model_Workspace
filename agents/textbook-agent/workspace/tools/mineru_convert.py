from __future__ import annotations

from typing import Any


def mineru_convert(book_id: str, pages: str = "") -> dict[str, Any]:
    """Queue a full MinerU re-import for one book. pages is reserved; convert is whole-book."""
    from app import library
    from mineru_http import buddy_post

    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    meta = library.get_book(sid)
    if meta is None:
        raise ValueError(f"book not found: {sid}")
    if str(meta.get("status") or "") == "draft":
        library.patch_book(sid, status="uploaded")
    out = buddy_post(f"/api/v1/admin/kb/convert/{sid}")
    if pages.strip():
        out["pages_ignored"] = pages.strip()
        out["note"] = "convert 是整书重跑；指定页请用 mineru_dump(page_ranges=...)"
    return out
