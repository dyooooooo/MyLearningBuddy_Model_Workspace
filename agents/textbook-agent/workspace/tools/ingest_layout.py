"""Ingest MinerU artifacts into the canonical IR (writes ``textbooks/<id>/ir/``).

Tool entry. ``apply=scan`` reports without writing; ``apply=fix`` writes
``ir/pages/NNNN.json`` and, when re-ingestion changes block ids,
``ir/remap.json``. Never touches ``pages/`` / ``mid/`` / ``v2md/``.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ir_lib import (book_dir, compute_remap, default_box_prefixes, ingest_page,
                    load_layout, load_model, load_v1, safe_book_id)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _page_count(book: Path) -> int:
    meta = book / "meta.json"
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            n = int(data.get("page_count") or 0)
            if n > 0:
                return n
        except (OSError, ValueError):
            pass
    layout = load_layout(book) or {}
    info = layout.get("pdf_info")
    return len(info) if isinstance(info, list) else 0


def ingest_layout(book_id: str, page_no: int = 0, root: str = "",
                  apply: str = "scan") -> dict[str, Any]:
    sid = safe_book_id(book_id)
    mode = (apply or "scan").strip().lower()
    book = book_dir(sid, root)
    layout = load_layout(book)
    if not layout:
        return {"ok": False, "book_id": sid, "reason": "missing layout.json"}
    box_prefixes = default_box_prefixes()
    model = load_model(book)
    v1 = load_v1(book)
    raw_total = _page_count(book)
    # meta.json 可能撒谎（与 layout 实际页数不符）：钳到 pdf_info 长度，
    # 否则多算 pages 报幻错误、少算漏页
    info = layout.get("pdf_info")
    lay_total = len(info) if isinstance(info, list) else 0
    total = min(raw_total, lay_total) if lay_total else raw_total
    wanted = [int(page_no)] if int(page_no or 0) > 0 else list(range(1, total + 1))

    ir_dir = book / "ir"
    pages_dir = ir_dir / "pages"
    old_ids: dict[int, list[dict[str, Any]]] = {}
    if mode == "fix" and pages_dir.is_dir():
        for path in sorted(pages_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                old_ids[int(data.get("page") or 0)] = list(data.get("blocks") or [])
            except (OSError, ValueError):
                continue

    stats: Counter[str] = Counter()
    new_pages: dict[int, dict[str, Any]] = {}
    errors: list[str] = []
    for n in wanted:
        try:
            page = ingest_page(book, n, box_prefixes, model=model, v1=v1, layout=layout)
        except (OSError, ValueError, FileNotFoundError) as exc:
            errors.append(f"p{n}: {exc}")
            continue
        new_pages[n] = page
        stats["pages"] += 1
        for blk in page["blocks"]:
            stats["blocks"] += 1
            stats[f"kind:{blk['kind']}"] += 1
            stats[f"type:{blk['type']}"] += 1
            if blk.get("row") is not None:
                stats["row_figures"] += 1
            if blk.get("latex"):
                stats["latex_joined"] += 1
            span_latex = sum(
                1
                for row in blk.get("lines") or []
                for span in row
                if isinstance(span, dict) and span.get("latex")
            )
            if span_latex:
                stats["span_latex"] += span_latex
            if blk.get("score") is not None:
                stats["scored"] += 1
            if blk.get("caption"):
                stats["captioned"] += 1
        stats["footnotes"] += len(page["chrome"]["footnotes"])
        stats["headers"] += len(page["chrome"]["headers"])

    remap = compute_remap(old_ids, new_pages) if old_ids else {}

    if mode == "fix":
        for n, page in new_pages.items():
            _atomic_write(
                pages_dir / f"{n:04d}.json",
                json.dumps(page, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            )
        if remap:
            _atomic_write(
                ir_dir / "remap.json",
                json.dumps(remap, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            )

    return {
        "ok": not errors,
        "book_id": sid,
        "apply": mode,
        "pages": stats["pages"],
        "total_pages": total,
        "blocks": stats["blocks"],
        "kinds": {k[5:]: v for k, v in stats.items() if k.startswith("kind:")},
        "types": {k[5:]: v for k, v in stats.items() if k.startswith("type:")},
        "footnotes": stats["footnotes"],
        "chrome_headers": stats["headers"],
        "row_figures": stats["row_figures"],
        "latex_joined": stats["latex_joined"],
        "span_latex": stats["span_latex"],
        "scored_blocks": stats["scored"],
        "captioned": stats["captioned"],
        "remapped": len(remap),
        "errors": errors[:10],
    }
