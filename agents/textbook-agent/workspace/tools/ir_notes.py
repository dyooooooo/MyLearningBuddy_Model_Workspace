"""Annotation overlay and the old-line touchstone diff for the IR line.

``ir_note`` validates/applies ``ir/notes/NNNN.yaml`` overlays (never
writes the source IR; edits live in notes only). ``ir_diff`` renders an
IR page (with its notes) and compares against the frozen old line's
``pages/NNNN.md`` — read-only reference, the touchstone for whether
human edits can ride the annotation layer.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any

from ir_lib import (apply_notes, book_dir, default_box_prefixes, ingest_page,
                    load_layout, load_model, load_v1, render_page_md, safe_book_id)

_IMG_URL = re.compile(r"!\[[^\]]*\]\([^)]+\)")
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
    page = ingest_page(book, int(page_no), default_box_prefixes(),
                       model=load_model(book), v1=load_v1(book), layout=layout)
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


def ir_diff(book_id: str, page_no: int = 0, root: str = "", limit: int = 20) -> dict[str, Any]:
    """试金石：IR(+notes) 渲染 vs 老线 pages/NNNN.md（只读）。report 逐页相似度。"""
    sid = safe_book_id(book_id)
    book = book_dir(sid, root)
    layout = load_layout(book)
    if not layout:
        return {"ok": False, "book_id": sid, "reason": "missing layout.json"}
    info = layout.get("pdf_info") or []
    wanted = [int(page_no)] if int(page_no or 0) > 0 else [
        int(p.get("page_idx") or 0) + 1 for p in info if isinstance(p, dict)
    ][: int(limit or 20)]
    model = load_model(book)
    v1 = load_v1(book)
    box = default_box_prefixes()
    report = []
    for n in wanted:
        old_path = book / "pages" / f"{n:04d}.md"
        if not old_path.is_file():
            report.append({"page": n, "old": "missing"})
            continue
        old_md = old_path.read_text(encoding="utf-8", errors="replace")
        page = ingest_page(book, n, box, model=model, v1=v1, layout=layout)
        page, _rej = apply_notes(page, load_notes(book, n))
        new_lines = _normalize(render_page_md(page))
        old_lines = _normalize(old_md)
        ratio = difflib.SequenceMatcher(None, old_lines, new_lines).ratio()
        only_new = [l for l in difflib.unified_diff(old_lines, new_lines, lineterm="")
                    if l.startswith("+") and not l.startswith("+++")][:5]
        only_old = [l for l in difflib.unified_diff(old_lines, new_lines, lineterm="")
                    if l.startswith("-") and not l.startswith("---")][:5]
        report.append({
            "page": n,
            "similarity": round(ratio, 4),
            "ir_plus": only_new,
            "old_plus": only_old,
        })
    sims = [r["similarity"] for r in report if "similarity" in r]
    return {
        "ok": True,
        "book_id": sid,
        "pages": len(report),
        "mean_similarity": round(sum(sims) / len(sims), 4) if sims else None,
        "min_similarity": min(sims) if sims else None,
        "report": report,
    }
