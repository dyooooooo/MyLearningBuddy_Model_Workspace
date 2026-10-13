"""Small agent for a printed table of contents, any textbook.

Python drafts the rows from the print. The local 35B reads a page only when the
text layer is thin, then judges joins, splits and levels on every catalog page.
Tools in ``printed_toc_tools``:

1. toc_locate   catalog pages (four signals; 35B on thumbnails)
2. toc_facts    text lines with box, size, weight per catalog page
3. toc_assemble rows and levels by print style, whole book at once (draft)
4. toc_review   35B judges joins, splits and levels; body titles are reference
5. toc_fill     page numbers missing from the text layer (35B on crops)
6. toc_verify   offset from running page numbers; every label looked up on
                the body page it points to; letters MinerU never read flagged
7. page order   a printed page smaller than the one before is flagged

arch="v2" (default) replaces steps 3–4 and the English-only page bars: the model decides
every line under a JSON schema, Python checks facts, disagreements are asked
again (``printed_toc_decide``). Each entry then carries ``decision``.

Writes ``textbooks/<id>/toc.print``, then ``ir_print_toc`` compiles it into
``ir/print_toc.json`` for the page-ir surface and ``ir_toc`` rebuilds the
sidebar ``ir/toc.json`` on that skeleton. ``toc_golden`` compares it with the
frozen answers in eval/printed_toc/.
"""
from __future__ import annotations

import json
import time
from typing import Any

import ir_print_toc
import ir_toc
import printed_toc_decide as decide
import printed_toc_tools as tools

VERSION = "printed_toc v2 layout-svc"
SCHEMA_VERSION = 2
ARCHES = ("v1", "v2")


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
    fact = row.get("fact_method") or method
    if fact == "text_layer":
        return "text_layer"
    if fact == "crops":
        return "crops"
    return "model_lines"


def _evidence(row: dict[str, Any], method: str) -> dict[str, Any]:
    fact = row.get("fact_method") or method
    if fact == "crops":
        label_from = "crops"
    elif fact == "text_layer":
        label_from = "text_layer"
    else:
        label_from = "model_lines"
    if row.get("filled"):
        page_from = "filled"
    elif row.get("print_page") is not None:
        page_from = "print"
    else:
        page_from = "none"
    return {
        "label_from": label_from,
        "page_from": page_from,
        "level_from": row.get("level_from") or "style",
        "level_conf": row.get("level_conf") or "high",
    }


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
        "evidence": _evidence(row, method),
    }
    if row.get("level_draft") is not None:
        out["level_draft"] = row["level_draft"]
    if row["hole"] and row["print_page"] is None:
        out["page_unread"] = row["hole"]
    for key in ("text_suspect", "mineru_text"):
        if row.get(key):
            out[key] = row[key]
    if row.get("decision") is not None:
        out["decision"] = row["decision"]
        if row["decision"].get("needs_review"):
            out["needs_review"] = True
    return out


def _load_old(book) -> dict[str, Any] | None:
    path = book / "toc.print"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def arch_inputs(base: dict[str, Any], arch: str) -> dict[str, Any]:
    """Cache fingerprint for one arch. v1 keeps the original keys, so old v1 caches still match."""
    if arch != "v2":
        return base
    inputs = dict(base)
    inputs["arch"] = "v2"
    inputs["prompts"] = dict(base.get("prompts") or {})
    inputs["prompts"]["decide"] = tools._file_sha(tools._home() / "prompts" / "printed_toc_decide.md")
    inputs["prompts"]["pair"] = tools._file_sha(tools._home() / "prompts" / "printed_toc_pair.md")
    return inputs


def _structure_v1(book, rows, pages_meta, collected, toc_pages) -> tuple[list[dict[str, Any]], float]:
    sk = tools.book_skeletons(book, rows) if rows else {"order": [], "expect": []}
    split_notes: list = []
    if rows:
        rows, split_notes = tools.apply_skeleton_splits(rows, sk)
    review_s, rejected = tools.review(book, rows, collected, toc_pages)
    for meta in pages_meta:
        meta["rejected_proposals"] = rejected.get(int(meta.get("page") or 0), [])
    if pages_meta and split_notes:
        pages_meta[0]["rejected_proposals"] = list(pages_meta[0]["rejected_proposals"]) + split_notes
    tools.deepen_model_details(rows)
    tools.mark_neighbor_holes(rows)
    return rows, review_s


def _structure_v2(book, rows, pages_meta) -> tuple[list[dict[str, Any]], float]:
    if not rows:
        return rows, 0.0
    entries, notes, seconds = decide.decide_catalog(book, rows, pages_meta)
    for meta in pages_meta:
        meta["decide"] = notes.get(int(meta.get("page") or 0), {})
    return entries, seconds


def printed_toc_agent(
    book_id: str,
    pages: str = "",
    root: str = "",
    force: bool = False,
    arch: str = "v2",
) -> dict[str, Any]:
    """Locate, read and check one book's printed catalog. Writes toc.print only.

    pages: catalog PDF pages like "4,5" to skip locating; empty = locate.
    force: ignore the inputs fingerprint cache and run again.
    arch: "v2" model decides, facts check (default); "v1" draft + gated review.
    """
    if arch not in ARCHES:
        raise ValueError(f"arch must be one of {ARCHES}")
    started = time.perf_counter()
    book = tools.book_dir(book_id, root)
    layout_info = tools.layout_health()
    inputs = arch_inputs(tools.inputs_fingerprint(book, layout_info), arch)
    old = _load_old(book)
    if tools.cache_usable(old, inputs, force):
        old["cache"] = "hit"
        return {
            "file": str(book / "toc.print"),
            "entry_count": len(old.get("entries") or []),
            "method": old.get("method"),
            "toc_pages": old.get("toc_pages") or [],
            "page_offset": old.get("page_offset"),
            "cache": "hit",
            "timing_s": old.get("timing_s") or {},
        }
    collected = tools.collect_pages(book)
    given = [int(p) for p in pages.replace(",", " ").split() if p.isdigit()]
    found = {
        "route": "given",
        "candidates": [given],
        "toc_pages": given,
        "seconds": [],
        "scores": {},
        "disagree": [],
    }
    if not given:
        found = tools.locate(book, collected)
    toc_pages = found["toc_pages"]
    if toc_pages:
        rows, method, facts_s, pages_meta = tools._catalog(book, toc_pages, model_wraps=arch == "v1")
    else:
        rows, method, facts_s, pages_meta = [], "none", 0.0, []
    if arch == "v2":
        rows, review_s = _structure_v2(book, rows, pages_meta)
    else:
        rows, review_s = _structure_v1(book, rows, pages_meta, collected, toc_pages)
    fill_s = tools.fill(book, rows, method)
    if tools.skeleton.clear_backward_pages(rows):
        fill_s += tools.fill(book, rows, method)
        tools.skeleton.clear_backward_pages(rows)
    reread_s, absent = tools.reread_slot_gaps(book, rows) if rows else (0.0, [])
    if pages_meta:
        pages_meta[0]["absent_in_print"] = absent
        pages_meta[0]["level_offset"] = None
    fill_s = round(fill_s + reread_s, 3)
    offset, missing = tools.verify(book, rows, collected, toc_pages)
    tools.check_text(rows, collected)
    entries = [_entry(row, method) for row in rows]
    _mark_page_order(entries)
    doc = {
        "schema_version": SCHEMA_VERSION,
        "version": VERSION,
        "arch": arch,
        "book_id": book.name,
        "found": bool(entries),
        "route": found["route"],
        "candidates": found["candidates"],
        "toc_pages": toc_pages,
        "method": method,
        "page_offset": offset,
        "inputs": inputs,
        "pages_meta": pages_meta,
        "scores": found.get("scores") or {},
        "disagree": found.get("disagree") or [],
        "missing_in_toc": {"count": len(missing), "items": missing},
        "entries": entries,
        "timing_s": {
            "locate": found["seconds"],
            "facts": facts_s,
            "review": review_s,
            "fill": fill_s,
            "total": round(time.perf_counter() - started, 3),
        },
    }
    out = book / "toc.print"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    try:
        shown = ir_print_toc.ir_print_toc(book.name, str(book.parent))
    except (OSError, ValueError) as exc:
        shown = {"ok": False, "reason": str(exc)[:200]}
    try:
        sidebar = ir_toc.ir_toc(book.name, str(book.parent), "fix")
    except (OSError, ValueError) as exc:
        sidebar = {"ok": False, "reason": str(exc)[:200]}
    holes = sum(1 for e in entries if e.get("page_unread"))
    return {
        "file": str(out),
        "ir_print_toc": shown,
        "ir_toc": sidebar,
        "entry_count": len(entries),
        "method": method,
        "toc_pages": toc_pages,
        "page_offset": offset,
        "unread_pages": holes,
        "missing_in_toc": len(missing),
        "needs_review": sum(1 for e in entries if e.get("needs_review")),
        "arch": arch,
        "cache": "miss",
        "verified": {k: sum(1 for e in entries if e["verified"] == k)
                     for k in ("body", "body_near", "not_found", "unchecked", "no_page")},
        "timing_s": doc["timing_s"],
    }
