"""Engine-agnostic layout enrichment for textbook DocumentBlock IR (slim)."""
from __future__ import annotations

import re
from typing import Any

from app.services.document_ir import DocumentBlock, normalize_figure_caption, stable_block_id
from app.services.ir.column_detect import assign_layout_zone, detect_columns
from app.services.ir.textbook_profile import default_profile_id, load_textbook_profile

_FIG_CAP_RE = re.compile(r"^图[\s　]*[０-９0-9]+[\.．-][０-９0-9\.．-]*")
_NOISE_TEXT = frozenset({"?", "？", "#"})


def _pdf_page_size(pdf_bytes: bytes | None, page_no: int) -> tuple[float, float]:
    if not pdf_bytes:
        return 596.0, 843.0
    try:
        import fitz
    except ImportError:
        return 596.0, 843.0
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(page_no - 1)
        return float(page.rect.width), float(page.rect.height)
    finally:
        doc.close()


def normalize_bbox_to_1000(
    bbox: list[float] | None,
    page_w: float,
    page_h: float,
) -> list[float] | None:
    if not bbox or len(bbox) < 4:
        return None
    x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    if max(x0, y0, x1, y1) <= 1000 and x1 <= 1000 and y1 <= 1000:
        return [x0, y0, x1, y1]
    if page_w <= 0 or page_h <= 0:
        return [x0, y0, x1, y1]
    return [
        x0 / page_w * 1000,
        y0 / page_h * 1000,
        x1 / page_w * 1000,
        y1 / page_h * 1000,
    ]


def _block_text(block: DocumentBlock) -> str:
    return (block.text or block.caption or "").strip()


def _is_running_chrome(block: DocumentBlock) -> bool:
    mineru_type = str((block.meta or {}).get("mineru_type") or "")
    role = str((block.meta or {}).get("pedagogy_role") or "")
    if role in ("running_header", "page_number", "running_footer"):
        return True
    return mineru_type in (
        "page_number",
        "page_header",
        "page_footer",
        "footer",
        "header",
    )


def _chrome_slot_from_zone(zone: str) -> str:
    if zone in ("aside_left", "aside_right", "main"):
        return zone
    return "main"


def _assign_figure_groups(page_blocks: list[DocumentBlock], profile: dict[str, Any]) -> None:
    fig_cfg = profile.get("figure_group") or {}
    y_tol = float(fig_cfg.get("y_tolerance", 35))
    cap_re: re.Pattern[str] | None = profile.get("_fig_caption_re")

    figures = [
        b
        for b in page_blocks
        if b.type == "figure" and not (b.meta or {}).get("figure_group_id")
    ]
    if len(figures) < 2:
        return

    groups: list[list[DocumentBlock]] = []
    sorted_figs = sorted(figures, key=lambda b: (bbox_y0(b), b.order))
    current: list[DocumentBlock] = []
    last_y: float | None = None
    last_cap: str | None = None

    for fig in sorted_figs:
        y0 = bbox_y0(fig)
        cap = normalize_figure_caption(fig.caption) or _block_text(fig)
        cap_key = cap if cap and cap_re and cap_re.search(cap) else None
        if (
            current
            and last_y is not None
            and abs(y0 - last_y) <= y_tol
            and (cap_key is None or cap_key == last_cap or last_cap is None)
        ):
            current.append(fig)
        else:
            if current:
                groups.append(current)
            current = [fig]
            last_cap = cap_key
        last_y = y0
    if current:
        groups.append(current)

    for group in groups:
        if len(group) < 2:
            continue
        gid = stable_block_id("figgrp", str(group[0].page_no), group[0].id, group[-1].id)
        for fig in group:
            idx = page_blocks.index(fig)
            meta = dict(fig.meta)
            meta["figure_group_id"] = gid
            page_blocks[idx] = fig.model_copy(update={"meta": meta})


def bbox_y0(block: DocumentBlock) -> float:
    if block.bbox and len(block.bbox) >= 2:
        return float(block.bbox[1])
    return float(block.order)


def enrich_page_blocks_slim(
    page_blocks: list[DocumentBlock],
    *,
    profile: dict[str, Any],
    page_w: float,
    page_h: float,
) -> list[DocumentBlock]:
    zones_cfg = profile.get("zones") or {}
    left_x1_max = float(zones_cfg.get("aside_left_x1_max", 280))
    right_x0_min = float(zones_cfg.get("aside_right_x0_min", 680))

    # Pass 1: normalize bbox + drop noise
    normalized: list[DocumentBlock] = []
    for block in page_blocks:
        text = _block_text(block)
        if text in _NOISE_TEXT and block.type == "paragraph":
            meta = dict(block.meta or {})
            meta["skip_render"] = True
            meta["layout_zone"] = "running"
            normalized.append(block.model_copy(update={"meta": meta}))
            continue
        nb = normalize_bbox_to_1000(block.bbox, page_w, page_h)
        normalized.append(block.model_copy(update={"bbox": nb or block.bbox}))

    # Infer columns from content blocks (exclude page_header/number — they
    # create false gutters). Chrome still gets chrome_slot from the layout.
    content_bboxes = [
        b.bbox
        for b in normalized
        if b.bbox and not _is_running_chrome(b) and b.meta.get("layout_zone") != "running"
    ]
    layout = detect_columns(
        content_bboxes or [b.bbox for b in normalized],
        left_x1_max=left_x1_max,
        right_x0_min=right_x0_min,
    )

    enriched: list[DocumentBlock] = []
    for block in normalized:
        meta = dict(block.meta or {})
        figure_kind = meta.get("figure_kind")
        is_chrome = _is_running_chrome(block)

        if figure_kind == "page_scan":
            zone = "full_bleed"
        elif is_chrome:
            zone = "running"
            col = assign_layout_zone(block.bbox, layout)
            # Page numbers / headers sit in margins; they often have no
            # same-height main companion — fall back to x vs main edges.
            if col == "main" and block.bbox and len(block.bbox) >= 3:
                x0, x1 = float(block.bbox[0]), float(block.bbox[2])
                if x1 < x0:
                    x0, x1 = x1, x0
                if x1 <= layout.main_x0 + 8:
                    col = "aside_left"
                elif x0 >= layout.main_x1 - 8:
                    col = "aside_right"
                elif (x0 + x1) / 2.0 < 500:
                    col = "aside_left"
                else:
                    col = "aside_right"
            meta["chrome_slot"] = _chrome_slot_from_zone(col)
            meta["skip_render"] = True
            mineru_type = str(meta.get("mineru_type") or "")
            # Preserve 35B / fusion chrome roles — do not overwrite with mineru_type.
            refined = str(meta.get("refined_by") or "") in ("35b", "fusion")
            role_now = meta.get("pedagogy_role")
            if refined and role_now not in (None, "", "body"):
                pass
            elif role_now in (None, "", "body"):
                if mineru_type in ("page_header", "header"):
                    meta["pedagogy_role"] = "running_header"
                elif mineru_type == "page_number":
                    meta["pedagogy_role"] = "page_number"
                elif mineru_type in ("page_footer", "footer"):
                    meta["pedagogy_role"] = "running_footer"
                else:
                    y0 = float((block.bbox or [0, 500])[1])
                    meta["pedagogy_role"] = (
                        "running_header" if y0 < 200 else "page_number"
                    )
        elif meta.get("layout_zone") in ("aside_left", "aside_right", "main", "full_bleed"):
            # Preserve explicit zone from refine / upstream
            zone = str(meta.get("layout_zone"))
        else:
            zone = assign_layout_zone(block.bbox, layout)

        meta["layout_zone"] = zone
        meta.setdefault("pedagogy_role", "body")
        meta["column_count"] = layout.column_count
        meta["column_method"] = layout.method
        if block.bbox:
            meta.setdefault("layout_confidence", "high")
        else:
            meta.setdefault("layout_confidence", "low")
        enriched.append(block.model_copy(update={"meta": meta}))

    _assign_figure_groups(enriched, profile)

    order = 0
    final: list[DocumentBlock] = []
    for zone in ("main", "full_bleed", "aside_left", "aside_right"):
        zone_blocks = sorted(
            [b for b in enriched if b.meta.get("layout_zone") == zone],
            key=lambda b: (bbox_y0(b), b.order),
        )
        for b in zone_blocks:
            order += 1
            meta = dict(b.meta)
            meta["reading_order"] = order
            final.append(b.model_copy(update={"order": order, "meta": meta}))

    running = [b for b in enriched if b.meta.get("layout_zone") == "running"]
    for b in running:
        order += 1
        meta = dict(b.meta)
        meta["reading_order"] = order
        final.append(b.model_copy(update={"order": order, "meta": meta}))

    other = [
        b
        for b in enriched
        if b.meta.get("layout_zone")
        not in {"main", "full_bleed", "aside_left", "aside_right", "running"}
    ]
    for b in other:
        order += 1
        meta = dict(b.meta)
        meta.setdefault("layout_zone", "main")
        meta["reading_order"] = order
        final.append(b.model_copy(update={"order": order, "meta": meta}))

    return final


def enrich_blocks_slim(
    blocks: list[DocumentBlock],
    *,
    profile_id: str | None = None,
    pdf_bytes: bytes | None = None,
) -> list[DocumentBlock]:
    """Normalize bbox, detect columns, running chrome slots, reading order."""
    if not blocks:
        return []
    profile = load_textbook_profile(profile_id or default_profile_id())
    by_page: dict[int, list[DocumentBlock]] = {}
    for b in blocks:
        by_page.setdefault(b.page_no, []).append(b)

    out: list[DocumentBlock] = []
    for page_no in sorted(by_page.keys()):
        pw, ph = _pdf_page_size(pdf_bytes, page_no)
        out.extend(
            enrich_page_blocks_slim(by_page[page_no], profile=profile, page_w=pw, page_h=ph)
        )
    out.sort(key=lambda b: (b.page_no, b.order))
    return out


def enrich_blocks(
    blocks: list[DocumentBlock],
    *,
    profile_id: str | None = None,
    pdf_bytes: bytes | None = None,
) -> list[DocumentBlock]:
    """Alias for slim enrich (post-35B-refine pipeline)."""
    return enrich_blocks_slim(blocks, profile_id=profile_id, pdf_bytes=pdf_bytes)
