from __future__ import annotations

from typing import Any


def mineru_dump(book_id: str, page_ranges: str = "") -> dict[str, Any]:
    """Re-run MinerU JSON only (no page rewrite). page_ranges like 6-12."""
    from mineru_http import buddy_post

    sid = (book_id or "").strip()
    if not sid:
        raise ValueError("book_id required")
    q = f"?page_ranges={page_ranges.strip()}" if page_ranges.strip() else ""
    return buddy_post(f"/api/v1/admin/kb/mineru/dump/{sid}{q}")
