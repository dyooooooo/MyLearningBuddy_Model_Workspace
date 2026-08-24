"""Run MinerU and write per-page markdown into the Agent workspace."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.kb.markdown import blocks_to_page_markdown, group_blocks_by_page
from app.kb.mineru_client import parse_pdf
from app.kb.pdf_pages import open_pdf
from app.kb.settings import require_export_config
from app.kb.toc import extract_toc, refine_toc
from app.kb.workspace_store import (
    book_dir,
    book_to_dict,
    list_metas,
    pdf_path,
    read_meta,
    read_toc,
    write_meta,
    write_page,
    write_toc,
)

logger = logging.getLogger(__name__)


def process_kb_book(book_id: str) -> dict[str, Any]:
    require_export_config()
    meta = read_meta(book_id)
    if meta is None:
        raise ValueError(f"kb book not found: {book_id}")
    meta["status"] = "converting"
    meta["error"] = ""
    write_meta(book_id, meta)

    path = pdf_path(book_id)
    if not path.is_file():
        raise RuntimeError(f"PDF not found: {path}")
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError(f"PDF empty: {path}")

    parsed = parse_pdf(raw, filename=str(meta.get("filename") or "book.pdf"), book_id=book_id)
    grouped = group_blocks_by_page(list(parsed.blocks or []))
    toc = extract_toc(list(parsed.blocks or []))
    page_texts: dict[int, str] = {}

    with open_pdf(raw) as pages:
        total = pages.page_count()
        if total < 1:
            total = max(grouped.keys(), default=0)
        for page_no in range(1, total + 1):
            page_md = blocks_to_page_markdown(
                grouped.get(page_no, []),
                print_to_pdf=toc.get("print_to_pdf") if isinstance(toc.get("print_to_pdf"), dict) else None,
            )
            if not page_md.strip() and page_no == 1 and not grouped:
                page_md = (parsed.markdown or "").strip()
            page_texts[page_no] = page_md
            jpeg = pages.page_jpeg(page_no, dpi=120)
            write_page(book_id, page_no, page_md, jpeg)

    toc = refine_toc(toc, page_texts)
    write_toc(book_id, toc)
    meta["page_count"] = total
    meta["status"] = "ready"
    meta["error"] = ""
    write_meta(book_id, meta)
    logger.info("kb book ready id=%s pages=%d toc=%d", book_id, total, len(toc.get("entries") or []))
    return book_to_dict(meta)


def mark_failed(book_id: str, error: str) -> None:
    meta = read_meta(book_id)
    if meta is None:
        return
    meta["status"] = "failed"
    meta["error"] = (error or "")[:4000]
    write_meta(book_id, meta)


def create_kb_book(
    book_id: str,
    *,
    filename: str = "book.pdf",
    label: str = "",
    subject: str = "math",
    grade_band: str = "hs",
    textbook_hint: str = "",
    pdf_bytes: bytes = b"",
) -> dict[str, Any]:
    from app.kb.hint import clean_textbook_hint

    folder = book_dir(book_id)
    folder.mkdir(parents=True, exist_ok=True)
    if pdf_bytes:
        (folder / "book.pdf").write_bytes(pdf_bytes)
    hint = clean_textbook_hint(hint=textbook_hint, label=label, filename=filename)
    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "id": book_id,
        "label": label or hint or filename,
        "filename": filename or "book.pdf",
        "subject": subject or "math",
        "grade_band": grade_band or "hs",
        "textbook_hint": hint,
        "status": "uploaded",
        "page_count": 0,
        "error": "",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    }
    write_meta(book_id, meta)
    write_toc(book_id, {"entries": [], "print_to_pdf": {}, "toc_pages": []})
    return book_to_dict(meta)


def list_kb_books() -> list[dict[str, Any]]:
    rows = [book_to_dict(m) for m in list_metas()]
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows


def rebuild_toc_from_pages(book_id: str) -> dict:
    meta = read_meta(book_id)
    if meta is None:
        raise ValueError("book not found")
    toc = read_toc(book_id)
    folder = book_dir(book_id) / "pages"
    page_texts: dict[int, str] = {}
    if folder.is_dir():
        for path in folder.glob("*.md"):
            try:
                page_texts[int(path.stem)] = path.read_text(encoding="utf-8")
            except ValueError:
                continue
    toc = refine_toc(toc, page_texts)
    write_toc(book_id, toc)
    return toc


def get_toc(book_id: str) -> dict:
    return read_toc(book_id)
