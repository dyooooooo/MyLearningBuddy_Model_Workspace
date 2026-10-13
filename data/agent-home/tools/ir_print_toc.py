"""IR-line compile step: printed catalog ``toc.print`` -> ``ir/print_toc.json``.

The page-ir surface paints a catalog page from this file instead of the
MinerU pieces. Reads ``toc.print`` and the catalog pages' IR blocks (for
the printed heading). Writes only ``ir/print_toc.json``; removes it when
``toc.print`` has no catalog. Never writes ``toc.print``, ``ir/pages``,
``ir/notes`` or ``ir/toc.json``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ir_lib import book_dir, load_page_json, safe_book_id

SCHEMA = 1
LEVEL_MAX = 4
_TOC_WORD = re.compile(r"^(目录|contents)$", re.I)


def _out_path(book: Path) -> Path:
    return book / "ir" / "print_toc.json"


def _read_print(book: Path) -> tuple[dict[str, Any] | None, str]:
    path = book / "toc.print"
    if not path.is_file():
        return None, ""
    raw = path.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, ""
    return (doc if isinstance(doc, dict) else None), hashlib.sha256(raw).hexdigest()[:16]


def page_heading(page: dict[str, Any] | None) -> str:
    """The printed catalog word on this IR page (目录 / Contents), spaces dropped."""
    for block in (page or {}).get("blocks") or []:
        text = re.sub(r"\s+", "", str((block or {}).get("text") or ""))
        if _TOC_WORD.match(text):
            return text
    return ""


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def compile_row(entry: dict[str, Any], offset: int | None) -> dict[str, Any]:
    printed = _int(entry.get("print_page"))
    goto = printed + offset if printed is not None and offset is not None else None
    level = _int(entry.get("level")) or 0
    return {
        "label": str(entry.get("label") or "").strip(),
        "level": max(0, min(LEVEL_MAX, level)),
        "chapter": bool(entry.get("is_chapter")),
        "print_page": printed,
        "goto_page": goto if goto and goto > 0 else None,
        "needs_review": bool(entry.get("needs_review")),
    }


def compile_print_toc(
    doc: dict[str, Any], headings: dict[int, str], source_sha: str = ""
) -> dict[str, Any] | None:
    """Group the catalog entries by the PDF page they are printed on. None = nothing to show."""
    entries = doc.get("entries") if isinstance(doc.get("entries"), list) else []
    if not doc.get("found") or not entries:
        return None
    offset = _int(doc.get("page_offset"))
    pages: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pdf = _int(entry.get("pdf_page"))
        row = compile_row(entry, offset)
        if pdf is None or pdf <= 0 or not row["label"]:
            continue
        pages.setdefault(str(pdf), {"heading": headings.get(pdf, ""), "rows": []})["rows"].append(row)
    if not pages:
        return None
    return {
        "schema": SCHEMA,
        "source": "toc.print",
        "source_sha": source_sha,
        "arch": doc.get("arch"),
        "page_offset": offset,
        "pages": pages,
    }


def _write(out: Path, data: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"print_toc.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(out)


def _textbooks_root(root: str = "") -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    return base / "textbooks" if (base / "textbooks").is_dir() else base


def _book_ids(root: str = "") -> list[str]:
    folder = _textbooks_root(root)
    if not folder.is_dir():
        return []
    return [
        path.name
        for path in sorted(folder.iterdir())
        if path.is_dir() and (path / "ir").is_dir() and ((path / "toc.print").is_file() or _out_path(path).is_file())
    ]


def ir_print_toc(book_id: str, root: str = "") -> dict[str, Any]:
    """Compile toc.print into ir/print_toc.json. book_id=* or empty = every book with ir/."""
    raw_id = (book_id or "").strip()
    if raw_id in ("", "*"):
        results = [ir_print_toc(bid, root) for bid in _book_ids(root)]
        return {
            "ok": all(r.get("ok") for r in results),
            "book_id": "*",
            "books": len(results),
            "written": sum(1 for r in results if r.get("written")),
            "removed": sum(1 for r in results if r.get("removed")),
        }
    sid = safe_book_id(raw_id)
    book = book_dir(sid, root)
    if not (book / "ir").is_dir():
        return {"ok": False, "book_id": sid, "reason": "missing ir/"}
    out = _out_path(book)
    doc, sha = _read_print(book)
    built = None
    if doc is not None:
        pdf_pages = {_int(e.get("pdf_page")) for e in doc.get("entries") or [] if isinstance(e, dict)}
        headings = {p: page_heading(load_page_json(book, p)) for p in pdf_pages if p}
        built = compile_print_toc(doc, headings, sha)
    if built is None:
        removed = out.is_file()
        if removed:
            out.unlink()
        return {"ok": True, "book_id": sid, "written": False, "removed": removed}
    _write(out, built)
    return {
        "ok": True,
        "book_id": sid,
        "written": True,
        "file": str(out),
        "pages": sorted(int(p) for p in built["pages"]),
        "rows": sum(len(item["rows"]) for item in built["pages"].values()),
    }
