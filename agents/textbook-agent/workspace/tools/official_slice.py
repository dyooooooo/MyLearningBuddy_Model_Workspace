"""Read-only official page markdown from MinerU's per-page IR."""
from __future__ import annotations

from typing import Any

from official_lib import book_dir, render_official_page, rewrite_image_urls, safe_book_id


def slice_official(book_id: str, page_no: int, root: str = "") -> dict[str, Any]:
    """Return official Markdown for page_no. Does not write disk."""
    sid = safe_book_id(book_id)
    try:
        n = int(page_no)
    except (TypeError, ValueError):
        n = 0
    if n < 1:
        return {
            "ok": False,
            "book_id": sid,
            "page_no": n,
            "markdown": "",
            "reason": "官方稿本页切不开",
            "via": "",
        }
    book = book_dir(sid, root)
    markdown, via = render_official_page(book, n)
    if not markdown.strip():
        return {
            "ok": False,
            "book_id": sid,
            "page_no": n,
            "markdown": "",
            "reason": "官方稿本页切不开",
            "via": via,
        }
    return {
        "ok": True,
        "book_id": sid,
        "page_no": n,
        "markdown": rewrite_image_urls(markdown.strip() + "\n", sid),
        "reason": "",
        "via": via,
    }


def official_slice(book_id: str, page_no: int, root: str = "") -> dict[str, Any]:
    """Tool entry: official MinerU markdown for one page from layout.json or v1 list."""
    return slice_official(book_id, page_no, root=root)
