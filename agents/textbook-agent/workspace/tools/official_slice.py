"""Read-only official full.md slice aligned by MinerU v1 page_idx."""
from __future__ import annotations

from typing import Any

from official_lib import (
    book_dir,
    find_official_span,
    load_v1_pages,
    page_starts,
    rewrite_image_urls,
    safe_book_id,
)


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
        }
    book = book_dir(sid, root)
    full_path = book / "mineru" / "full.md"
    if not full_path.is_file():
        return {
            "ok": False,
            "book_id": sid,
            "page_no": n,
            "markdown": "",
            "reason": "官方稿本页切不开",
        }
    try:
        pages = load_v1_pages(book)
        full = full_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, FileNotFoundError):
        return {
            "ok": False,
            "book_id": sid,
            "page_no": n,
            "markdown": "",
            "reason": "官方稿本页切不开",
        }
    starts = page_starts(full, pages)
    span = find_official_span(full, pages, n, starts=starts)
    if span is None:
        return {
            "ok": False,
            "book_id": sid,
            "page_no": n,
            "markdown": "",
            "reason": "官方稿本页切不开",
        }
    left, right = span
    markdown = rewrite_image_urls(full[left:right].strip() + "\n", sid)
    return {
        "ok": True,
        "book_id": sid,
        "page_no": n,
        "markdown": markdown,
        "reason": "",
    }


def official_slice(book_id: str, page_no: int, root: str = "") -> dict[str, Any]:
    """Tool entry: slice official MinerU full.md for one page using v1 page_idx."""
    return slice_official(book_id, page_no, root=root)
