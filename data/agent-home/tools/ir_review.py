"""Flag low-confidence IR pages for the knowledge-base Agent.

Writes only ``textbooks/<id>/ir/review.json``. Corrections go in
``ir/notes``. Never writes ``pages/`` / ``mid/``, never calls 35B or
buddy-api layout escalate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ir_lib import book_dir, load_page_json, safe_book_id

SCORE_FLOOR = 0.85
_REVIEW_NAME = "review.json"
_MAX_DEPTH = 8
_MAX_BANDS = 16


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _walk_tree(node: dict[str, Any], depth: int = 0) -> list[dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    rows = [{"node": node, "depth": depth}]
    for child in node.get("children") or []:
        if isinstance(child, dict):
            rows.extend(_walk_tree(child, depth + 1))
    return rows


def _page_hits(page: dict[str, Any]) -> dict[str, Any] | None:
    layout = page.get("layout") if isinstance(page.get("layout"), dict) else {}
    blocks = [b for b in (page.get("blocks") or []) if isinstance(b, dict)]
    reasons: list[str] = []
    flagged: list[dict[str, Any]] = []
    for blk in blocks:
        why: list[str] = []
        score = blk.get("score")
        typ = str(blk.get("type") or "")
        if score is not None:
            try:
                if float(score) < SCORE_FLOOR:
                    why.append("score")
            except (TypeError, ValueError):
                why.append("score")
        elif typ in {"text", "title", "interline_equation"} and not str(blk.get("id") or "").startswith(
            ("kb-close-", "kb-algo-")
        ):
            why.append("unscored")
        if not why:
            continue
        flagged.append(
            {
                "id": blk.get("id"),
                "type": typ,
                "text": str(blk.get("text") or "")[:80],
                "why": why,
            }
        )

    tree = layout.get("tree") if isinstance(layout.get("tree"), dict) else {}
    walked = _walk_tree(tree) if tree else []
    bands = [row for row in walked if row["depth"] == 1]
    if len(bands) >= _MAX_BANDS:
        reasons.append("too_many_bands")
    if any(row["depth"] >= _MAX_DEPTH for row in walked):
        reasons.append("tree_depth")

    tracks = layout.get("tracks") if isinstance(layout.get("tracks"), dict) else {}
    if not tracks.get("main") and tree:
        reasons.append("track_missing")
    aside = tracks.get("aside")
    if aside and not tracks.get("aside_side"):
        reasons.append("track_side")

    members_by_band: dict[int, set[int]] = {}
    for i, row in enumerate(assembled_rows(layout, tree)):
        for idx in row:
            members_by_band.setdefault(i, set()).add(idx)
    close_cross = 0
    for blk in blocks:
        if not str(blk.get("id") or "").startswith("kb-close-"):
            continue
        bands_hit = {bi for bi, ids in members_by_band.items() if any(
            0 <= int(src) < len(blocks) and blocks[int(src)].get("id") == blk.get("id")
            for src in ids
        )}
        # 收口块自己的 bbox 跨过图带：看 close 行是否同时出现在多个 band 槽
        slots = blk.get("source_ids") or []
        if len(slots) >= 4:
            close_cross += 1
    if close_cross >= 3:
        reasons.append("close_span")

    fig_missing_cap = [
        blk
        for blk in blocks
        if blk.get("kind") == "figure" and not str(blk.get("caption") or "").strip()
    ]
    if len(fig_missing_cap) >= 3:
        reasons.append("caption_loose")
        for blk in fig_missing_cap[:8]:
            flagged.append(
                {
                    "id": blk.get("id"),
                    "type": blk.get("type"),
                    "text": str(blk.get("text") or "")[:80],
                    "why": ["caption_loose"],
                }
            )

    folio = str(layout.get("folio") or "")
    chrome = page.get("chrome") if isinstance(page.get("chrome"), dict) else {}
    page_size = page.get("page_size") or []
    try:
        page_w = float(page_size[0]) if page_size else 0.0
    except (TypeError, ValueError, IndexError):
        page_w = 0.0
    if folio and page_w:
        for num in chrome.get("page_numbers") or []:
            if not isinstance(num, dict):
                continue
            bb = num.get("bbox") or []
            if len(bb) < 4:
                continue
            mid = (float(bb[0]) + float(bb[2])) / 2
            side = "verso" if mid < page_w * 0.5 else "recto"
            if side != folio and "folio_side" not in reasons:
                reasons.append("folio_side")

    if flagged and any("score" in f["why"] for f in flagged):
        reasons.append("low_score")
    if not reasons and not flagged:
        return None
    if not reasons and flagged and all(f["why"] == ["unscored"] for f in flagged):
        return None
    return {
        "page": int(page.get("page") or 0),
        "reasons": reasons or ["low_score"],
        "tracks": 2 if tracks.get("aside") else 1,
        "bands": len(bands),
        "depth": max((row["depth"] for row in walked), default=0),
        "blocks": flagged[:40],
    }


def assembled_rows(layout: dict[str, Any], tree: dict[str, Any]) -> list[list[int]]:
    if tree.get("kind") == "rows":
        kids = tree.get("children") or []
    elif tree:
        kids = [tree]
    else:
        kids = []
    out: list[list[int]] = []
    for kid in kids:
        if not isinstance(kid, dict):
            continue
        mems = [int(i) for i in (kid.get("members") or []) if i is not None]
        out.append(mems)
    return out


def review_from_pages(book_id: str, pages: dict[int, dict[str, Any]]) -> dict[str, Any]:
    hits = []
    for _n, page in sorted(pages.items()):
        hit = _page_hits(page)
        if hit:
            hits.append(hit)
    return {
        "book_id": book_id,
        "pages": hits,
        "page_count": len(hits),
        "block_count": sum(len(h.get("blocks") or []) for h in hits),
        "agent": "textbook-agent",
        "instruction": (
            "对照 open_pdf_page 与 ir/pages 原块，订正只写 ir/notes"
            "（replace / retitle / delete）。"
            "禁止写 pages/、mid/，禁止 refine_page，禁止 35B，禁止 buddy-api 版式升级。"
        ),
    }


def merge_review(book: Path, incoming: dict[str, Any], wanted: set[int]) -> dict[str, Any]:
    """Replace hits for ``wanted`` pages; keep the rest of an existing list."""
    path = book / "ir" / _REVIEW_NAME
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                existing = data
        except (OSError, ValueError):
            existing = {}
    keep = [
        p
        for p in (existing.get("pages") or [])
        if isinstance(p, dict) and int(p.get("page") or 0) not in wanted
    ]
    keep.extend(p for p in (incoming.get("pages") or []) if isinstance(p, dict))
    keep.sort(key=lambda p: int(p.get("page") or 0))
    out = dict(incoming)
    out["pages"] = keep
    out["page_count"] = len(keep)
    out["block_count"] = sum(len(h.get("blocks") or []) for h in keep)
    if existing.get("book_id") and not out.get("book_id"):
        out["book_id"] = existing["book_id"]
    return out


def write_review(book: Path, review: dict[str, Any]) -> Path:
    path = book / "ir" / _REVIEW_NAME
    _atomic_write(path, json.dumps(review, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return path


def _load_ir_pages(book: Path) -> dict[int, dict[str, Any]]:
    folder = book / "ir" / "pages"
    out: dict[int, dict[str, Any]] = {}
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            n = int(path.stem)
        except ValueError:
            continue
        page = load_page_json(book, n)
        if page:
            out[n] = page
    return out


def _ensure_review_task(book_id: str) -> dict[str, Any] | None:
    try:
        from create_task import create_task
        from list_tasks import list_tasks
    except Exception:
        return None
    title = f"复查低置信分栏与识别 · {book_id[:8]}"
    try:
        payload = list_tasks()
        rows = payload.get("tasks") if isinstance(payload, dict) else payload
        for row in rows or []:
            if isinstance(row, dict) and row.get("title") == title:
                return {"id": row.get("id"), "existed": True}
        return create_task(
            title=title,
            mode="trigger",
            trigger="tool.ingest_layout",
            condition="这本书的 ir/review.json 里还有待复查页",
            message=(
                f"书 {book_id}：读 ir/review.json，对列出的页用 open_pdf_page 对照原图，"
                "订正只写 ir/notes。禁止写 pages/、mid/，禁止 refine_page，"
                "禁止 35B，禁止 buddy-api 版式升级。"
            ),
        )
    except Exception:
        return None


def ir_review(book_id: str, root: str = "", apply: str = "scan") -> dict[str, Any]:
    """扫 ir/pages 里低置信分栏与识别；fix 只写 ir/review.json，交给知识库 Agent。"""
    sid = safe_book_id(book_id)
    mode = (apply or "scan").strip().lower()
    book = book_dir(sid, root)
    if not (book / "ir" / "pages").is_dir():
        return {"ok": False, "book_id": sid, "reason": "missing ir/pages"}
    review = review_from_pages(sid, _load_ir_pages(book))
    task = None
    if mode == "fix":
        write_review(book, review)
        if review.get("pages"):
            task = _ensure_review_task(sid)
    return {
        "ok": True,
        "book_id": sid,
        "apply": mode,
        "page_count": review["page_count"],
        "block_count": review["block_count"],
        "pages": [p["page"] for p in review["pages"]],
        "wrote": mode == "fix",
        "path": f"textbooks/{sid}/ir/{_REVIEW_NAME}" if mode == "fix" else "",
        "task": task,
        "instruction": review["instruction"],
    }
