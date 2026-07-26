"""Deterministic payload / layout heuristics for 35B post-VLM repair.

These rules are intentional self-built glue: short, stable, and tied to our
PageBlueprint contract. Heavy layout detection stays with the VLM / MinerU.
"""
from __future__ import annotations

import re
from typing import Any

_CAPTION_LABEL_RE = re.compile(r"^\s*[图表]\s*[0-9０-９\-—.·]+")
_PAGE_NUM_RE = re.compile(r"^[0-9０-９]{1,4}$")
_TOC_CHAPTER_RE = re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*章")
_SECTION_NUM_RE = re.compile(r"^\d+\.\d+")


def caption_is_body_text(cap: str) -> bool:
    """True caption is a short label (e.g. 「图 1-1-1」); long prose is body."""
    cap = (cap or "").strip()
    if len(cap) <= 30 or _CAPTION_LABEL_RE.match(cap):
        return False
    return "。" in cap or cap.count("，") >= 2


def is_real_figure_caption(cap: str) -> bool:
    return bool(_CAPTION_LABEL_RE.match((cap or "").strip()))


def figure_caption_should_demote(cap: str) -> bool:
    """Mis-labeled figure: body prose or TOC-like line without 图/表 label."""
    cap = (cap or "").strip()
    if not cap:
        return False
    if is_real_figure_caption(cap):
        return False
    if caption_is_body_text(cap):
        return True
    # Short non-label captions (目录行、章节名+页码) must not become <img>
    if "目录" in cap or _SECTION_NUM_RE.match(cap) or _TOC_CHAPTER_RE.match(cap):
        return True
    parts = cap.split()
    if parts and _PAGE_NUM_RE.fullmatch(parts[-1]) and len(cap) <= 40:
        return True
    return False


def table_looks_like_toc(rows: list[list[str]]) -> bool:
    """2-column title/page-number tables are TOC, not real tables."""
    if not rows or len(rows) < 2:
        return False
    page_hits = 0
    col2 = 0
    for row in rows:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if len(cells) >= 2:
            col2 += 1
            if _PAGE_NUM_RE.fullmatch(cells[-1]):
                page_hits += 1
        elif len(cells) == 1 and _PAGE_NUM_RE.fullmatch(cells[0]):
            page_hits += 1
    if col2 < max(2, len(rows) // 2):
        return False
    return page_hits >= max(2, (len(rows) * 2) // 3)


def should_reroute_aside_left(bbox: list[float], *, zone: str) -> bool:
    """Narrow left-gutter blocks wrongly marked zone=main → aside_left.

    Requires a tall vertical strip (人物简介/批注), not short copyright lines.
    """
    if zone != "main" or len(bbox) < 4:
        return False
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    width = x1 - x0
    height = y1 - y0
    # Entirely in left third; tall enough to be a sidebar column
    return x1 <= 380.0 and width <= 360.0 and x0 < 280.0 and height >= 120.0


def bbox_key(bbox: list[float], *, ndigits: int = 0) -> tuple[int, ...]:
    return tuple(int(round(float(v), ndigits)) for v in bbox[:4])


def duplicate_figure_bboxes(blocks: list[Any]) -> set[str]:
    """Block ids that share an identical figure bbox with another figure."""
    counts: dict[tuple[int, ...], list[str]] = {}
    for b in blocks:
        if getattr(b, "kind", None) != "figure":
            continue
        key = bbox_key(list(getattr(b, "bbox", []) or []))
        counts.setdefault(key, []).append(getattr(b, "id", ""))
    out: set[str] = set()
    for ids in counts.values():
        if len(ids) >= 2:
            out.update(ids)
    return out


def should_prefer_mineru_bbox(*, has_layout_hints: bool) -> bool:
    """Gate: when MinerU layout hints are present, prefer hint bboxes over VLM guesses.

    Used by post-VLM repair / normalize paths that may realign block geometry.
    """
    return bool(has_layout_hints)


def prefer_hint_bbox(
    block_bbox: list[float] | None,
    hint_bbox: list[float] | None,
    *,
    has_hints: bool,
) -> list[float]:
    """Return the bbox to keep when layout hints are trusted."""
    if should_prefer_mineru_bbox(has_layout_hints=has_hints) and hint_bbox and len(hint_bbox) >= 4:
        return [float(hint_bbox[0]), float(hint_bbox[1]), float(hint_bbox[2]), float(hint_bbox[3])]
    if block_bbox and len(block_bbox) >= 4:
        return [float(block_bbox[0]), float(block_bbox[1]), float(block_bbox[2]), float(block_bbox[3])]
    return list(hint_bbox or block_bbox or [])
