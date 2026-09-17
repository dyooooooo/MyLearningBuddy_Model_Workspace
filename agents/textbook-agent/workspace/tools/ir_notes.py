"""Annotation overlay for the IR line.

``ir_note`` validates/applies ``ir/notes/NNNN.yaml`` overlays (never
writes the source IR; edits live in notes only). The touchstone diff
lives in the sibling tool ``ir_diff``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ir_lib import (apply_notes, book_dir, load_layout, load_or_ingest_page,
                    render_page_md, safe_book_id)


def load_notes(book: Path, page_no: int) -> list[dict[str, Any]]:
    path = book / "ir" / "notes" / f"{int(page_no):04d}.yaml"
    if not path.is_file():
        return []
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [n for n in (data or []) if isinstance(n, dict)]


def ir_note(book_id: str, page_no: int, root: str = "", apply: str = "scan") -> dict[str, Any]:
    """校验并（fix 时）报告注解应用结果。不写任何源文件——notes 自己就是产物。"""
    sid = safe_book_id(book_id)
    mode = (apply or "scan").strip().lower()
    book = book_dir(sid, root)
    layout = load_layout(book)
    if not layout:
        return {"ok": False, "book_id": sid, "reason": "missing layout.json"}
    notes = load_notes(book, page_no)
    try:
        page, _wrote = load_or_ingest_page(book, int(page_no))
    except (FileNotFoundError, ValueError) as exc:
        return {"ok": False, "book_id": sid, "reason": str(exc)}
    overlaid, rejected = apply_notes(page, notes)
    rendered = render_page_md(overlaid)
    return {
        "ok": not rejected,
        "book_id": sid,
        "page_no": int(page_no),
        "apply": mode,
        "notes": len(notes),
        "rejected": rejected,
        "markdown": rendered if mode == "fix" else "",
    }
