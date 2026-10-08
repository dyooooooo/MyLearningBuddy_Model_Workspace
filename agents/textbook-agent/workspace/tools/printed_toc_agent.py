"""Small agent for a printed table of contents, any textbook.

Python fixes the order; facts come from the print, the local 35B only looks
where the print has no text. Tools in ``printed_toc_tools``:

1. toc_locate   catalog pages (35B on thumbnails)
2. toc_facts    text lines with box, size, weight per catalog page
                (PDF text layer; scanned books: 35B lines with boxes)
3. toc_assemble rows and levels by print style, whole book at once
4. toc_fill     page numbers missing from the text layer (35B on crops)
5. toc_verify   offset from running page numbers; every label looked up on
                the body page it points to; letters MinerU never read flagged
6. page order   a printed page smaller than the one before is flagged

Writes ``textbooks/<id>/toc.print`` only. ``toc_golden`` compares it with the
frozen answers in eval/printed_toc/.
"""
from __future__ import annotations

import json
import time
from typing import Any

import printed_toc_tools as tools

VERSION = "printed_toc v2 facts-first"


def _mark_page_order(entries: list[dict[str, Any]]) -> None:
    last = 0
    for row in entries:
        page = row.get("print_page")
        if page is None:
            continue
        if page < last:
            row["page_order"] = "backward"
        else:
            last = page


def _source(row: dict[str, Any], method: str) -> str:
    if row.get("filled"):
        return "filled"
    return "text_layer" if method == "text_layer" else "model_lines"


def _entry(row: dict[str, Any], method: str) -> dict[str, Any]:
    out = {
        "label": row["text"],
        "level": row["level"],
        "is_chapter": row["level"] == 0,
        "print_page": row["print_page"],
        "pdf_page": row["pdf_page"],
        "source": _source(row, method),
        "verified": row.get("verified"),
        "style": {"size": row["size"], "bold": row["bold"], "indent": row["indent"]},
        "bbox": [round(v, 1) for v in (row["x0"], row["y0"], row["x1"], row["y1"])],
    }
    if row["hole"] and row["print_page"] is None:
        out["page_unread"] = row["hole"]
    for key in ("text_suspect", "mineru_text"):
        if row.get(key):
            out[key] = row[key]
    return out


def printed_toc_agent(book_id: str, pages: str = "", root: str = "") -> dict[str, Any]:
    """Locate, read and check one book's printed catalog. Writes toc.print only.

    pages: catalog PDF pages like "4,5" to skip locating; empty = locate with the 35B.
    """
    started = time.perf_counter()
    book = tools.book_dir(book_id, root)
    collected = tools.collect_pages(book)
    given = [int(p) for p in pages.replace(",", " ").split() if p.isdigit()]
    found = {"route": "given", "candidates": [given], "toc_pages": given, "seconds": []}
    if not given:
        found = tools.locate(book, collected)
    toc_pages = found["toc_pages"]
    rows, method, facts_s = tools._catalog(book, toc_pages) if toc_pages else ([], "none", 0.0)
    fill_s = tools.fill(book, rows, method)
    offset = tools.verify(book, rows, collected, toc_pages)
    tools.check_text(rows, collected)
    entries = [_entry(row, method) for row in rows]
    _mark_page_order(entries)
    doc = {
        "version": VERSION,
        "book_id": book.name,
        "found": bool(entries),
        "route": found["route"],
        "candidates": found["candidates"],
        "toc_pages": toc_pages,
        "method": method,
        "page_offset": offset,
        "entries": entries,
        "timing_s": {
            "locate": found["seconds"],
            "facts": facts_s,
            "fill": fill_s,
            "total": round(time.perf_counter() - started, 3),
        },
    }
    out = book / "toc.print"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    holes = sum(1 for e in entries if e.get("page_unread"))
    return {
        "file": str(out),
        "entry_count": len(entries),
        "method": method,
        "toc_pages": toc_pages,
        "page_offset": offset,
        "unread_pages": holes,
        "verified": {k: sum(1 for e in entries if e["verified"] == k)
                     for k in ("body", "body_near", "not_found", "unchecked", "no_page")},
        "timing_s": doc["timing_s"],
    }
