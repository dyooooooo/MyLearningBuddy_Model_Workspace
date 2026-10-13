"""Ingest MinerU artifacts into the canonical IR (writes ``textbooks/<id>/ir/``).

Tool entry. ``apply=scan`` reports without writing; ``apply=fix`` deletes the
page JSON it is about to replace, then writes new ``ir/pages/NNNN.json``.
Full-book success also drops leftover page files. When re-ingestion changes
block ids it writes ``ir/remap.json``. Never touches ``pages/`` / ``mid/`` /
``v2md/`` / ``ir/notes``.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ir_lib import (book_dir, compute_remap, default_box_prefixes, ingest_page,
                    ir_spans, load_layout, load_model, load_v1, load_v2, safe_book_id)
from ir_review import merge_review, review_from_pages, write_review
from ir_review import _ensure_review_task


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _clear_ir_pages(pages_dir: Path, page_nos: set[int] | None) -> int:
    """Delete existing IR page JSON. ``page_nos=None`` means every ``*.json``.

    Only ``ir/pages/*.json``. Never ``ir/notes``.
    """
    if not pages_dir.is_dir():
        return 0
    n = 0
    for path in list(pages_dir.glob("*.json")):
        if page_nos is not None:
            try:
                num = int(path.stem)
            except ValueError:
                continue
            if num not in page_nos:
                continue
        path.unlink()
        n += 1
    return n


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


def _textbooks_root(root: str = "") -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    return base / "textbooks" if (base / "textbooks").is_dir() else base


def _books_with_layout(root: str = "") -> list[str]:
    folder = _textbooks_root(root)
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.iterdir()):
        if path.is_dir() and (path / "mineru" / "layout.json").is_file():
            out.append(path.name)
    return out


def ingest_layout(book_id: str, page_no: int = 0, root: str = "",
                  apply: str = "scan") -> dict[str, Any]:
    raw_id = (book_id or "").strip()
    if raw_id in ("", "*"):
        results = [ingest_layout(bid, page_no, root, apply) for bid in _books_with_layout(root)]
        failed = [r.get("book_id") for r in results if not r.get("ok")]
        return {
            "ok": not failed,
            "book_id": "*",
            "apply": (apply or "scan").strip().lower(),
            "books": len(results),
            "pages": sum(int(r.get("pages") or 0) for r in results),
            "blocks": sum(int(r.get("blocks") or 0) for r in results),
            "failed": failed,
            "errors": [e for r in results for e in (r.get("errors") or [])][:20],
        }
    sid = safe_book_id(book_id)
    mode = (apply or "scan").strip().lower()
    book = book_dir(sid, root)
    layout = load_layout(book)
    if not layout:
        return {"ok": False, "book_id": sid, "reason": "missing layout.json"}
    box_prefixes = default_box_prefixes()
    model = load_model(book)
    v1 = load_v1(book)
    v2 = load_v2(book)
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
            page = ingest_page(book, n, box_prefixes, model=model, v1=v1, v2=v2, layout=layout)
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
                for span in ir_spans(blk.get("lines"))
                if span.get("latex")
            )
            if span_latex:
                stats["span_latex"] += span_latex
            if blk.get("score") is not None:
                stats["scored"] += 1
            if blk.get("caption"):
                stats["captioned"] += 1
            if str(blk.get("id") or "").startswith("kb-close-"):
                stats["close_blocks"] += 1
            if blk.get("slot") == "aside":
                stats["aside_blocks"] += 1
            if blk.get("slot") == "span":
                stats["span_blocks"] += 1
        stats["footnotes"] += len(page["chrome"]["footnotes"])
        stats["headers"] += len(page["chrome"]["headers"])
        if any(blk.get("slot") == "aside" for blk in page["blocks"]):
            stats["aside_pages"] += 1
        folio = str(page.get("folio") or "")
        if folio:
            stats[f"folio:{folio}"] += 1

    remap = compute_remap(old_ids, new_pages) if old_ids else {}
    review = review_from_pages(sid, new_pages)
    if int(page_no or 0) > 0:
        review = merge_review(book, review, set(new_pages))

    cleared = 0
    if mode == "fix" and new_pages:
        # 先读过 old_ids，再删盘上的页文件，再写新的。
        # 单页只清这一页；全书清成功页 + 不在 wanted 里的残页；失败页的旧文件留下。
        if int(page_no or 0) > 0:
            to_clear = set(new_pages)
        else:
            wanted_set = set(wanted)
            leftovers: set[int] = set()
            if pages_dir.is_dir():
                for path in pages_dir.glob("*.json"):
                    try:
                        num = int(path.stem)
                    except ValueError:
                        continue
                    if num not in wanted_set:
                        leftovers.add(num)
            to_clear = set(new_pages) | leftovers
        cleared = _clear_ir_pages(pages_dir, to_clear)
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
        write_review(book, review)
        if review.get("pages"):
            _ensure_review_task(sid)

    return {
        "ok": not errors,
        "book_id": sid,
        "apply": mode,
        "cleared": cleared,
        "pages": stats["pages"],
        "total_pages": total,
        "blocks": stats["blocks"],
        "kinds": {k[5:]: v for k, v in stats.items() if k.startswith("kind:")},
        "types": {k[5:]: v for k, v in stats.items() if k.startswith("type:")},
        "folio": {k[6:]: v for k, v in stats.items() if k.startswith("folio:")},
        "footnotes": stats["footnotes"],
        "chrome_headers": stats["headers"],
        "row_figures": stats["row_figures"],
        "latex_joined": stats["latex_joined"],
        "span_latex": stats["span_latex"],
        "scored_blocks": stats["scored"],
        "captioned": stats["captioned"],
        "aside_pages": stats["aside_pages"],
        "aside_blocks": stats["aside_blocks"],
        "span_blocks": stats["span_blocks"],
        "close_blocks": stats["close_blocks"],
        "review_pages": len(review.get("pages") or []),
        "remapped": len(remap),
        "errors": errors[:10],
    }
