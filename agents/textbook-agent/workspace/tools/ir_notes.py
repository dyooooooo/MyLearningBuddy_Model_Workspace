"""Annotation overlay for the IR line.

``ir_note`` validates/applies ``ir/notes/NNNN.yaml`` overlays (never
writes the source IR; edits live in notes only). The touchstone diff
lives in the sibling tool ``ir_diff``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ir_lib import (apply_notes, book_dir, default_box_prefixes, ingest_page,
                    load_layout, load_model, load_v1, render_page_md, safe_book_id)

_BLOCK_ID = re.compile(r"\s*\^kb-[0-9a-f]{6}\b")
_WS = re.compile(r"\s+")


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


def _normalize(md: str) -> list[str]:
    lines = []
    for line in (md or "").splitlines():
        line = _BLOCK_ID.sub("", line)
        line = _IMG_URL.sub("![图](IMG)", line)
        line = _WS.sub("", line)
        if line:
            lines.append(line)
    return lines


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
        page = ingest_page(book, int(page_no), default_box_prefixes(),
                           model=load_model(book), v1=load_v1(book), layout=layout)
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
