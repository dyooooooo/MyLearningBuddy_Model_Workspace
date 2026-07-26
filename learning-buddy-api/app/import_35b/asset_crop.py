"""Crop figure assets from original PDF pages according to blueprint bbox."""
from __future__ import annotations

import logging

from app.import_35b.assets import upload_import_asset
from app.import_35b.payload_heuristics import (
    bbox_key,
    figure_caption_should_demote,
)
from app.import_35b.schemas import PageBlueprint

logger = logging.getLogger(__name__)


def _norm_bbox_to_pdf_rect(bbox: list[float], page_w: float, page_h: float):
    import fitz

    x0, y0, x1, y1 = bbox
    return fitz.Rect(
        x0 / 1000.0 * page_w,
        y0 / 1000.0 * page_h,
        x1 / 1000.0 * page_w,
        y1 / 1000.0 * page_h,
    )


def _should_skip_crop(block, *, dup_keys: set[tuple[int, ...]]) -> bool:
    """Skip crop for TOC/body mis-labels and duplicate identical bboxes (p6)."""
    cap = block.content.figure_caption() or ""
    if figure_caption_should_demote(cap):
        return True
    key = bbox_key(list(block.bbox or []))
    return key in dup_keys


def crop_figure_assets(
    *,
    pdf_bytes: bytes,
    blueprint: PageBlueprint,
    asset_prefix: str,
    dpi: int = 180,
) -> PageBlueprint:
    """Crop `kind=figure` blocks to MinIO and write asset refs into content.

    Uses the VLM blueprint bbox as-is; accuracy is governed by the layout prompt.
    """
    if not pdf_bytes or not asset_prefix:
        return blueprint

    import fitz

    # Identical figure bboxes (directory-line spam) → skip all but first
    seen: dict[tuple[int, ...], int] = {}
    dup_keys: set[tuple[int, ...]] = set()
    for block in blueprint.blocks:
        if block.kind != "figure":
            continue
        key = bbox_key(list(block.bbox or []))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] >= 2:
            dup_keys.add(key)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(blueprint.page_no - 1)
        page_w, page_h = float(page.rect.width), float(page.rect.height)
        blocks = []
        cropped_keys: set[tuple[int, ...]] = set()
        for block in blueprint.blocks:
            if block.kind != "figure":
                blocks.append(block)
                continue
            if _should_skip_crop(block, dup_keys=dup_keys):
                blocks.append(block)
                continue
            key = bbox_key(list(block.bbox or []))
            # Only crop the first occurrence of a duplicated bbox
            if key in cropped_keys:
                blocks.append(block)
                continue
            try:
                clip = _norm_bbox_to_pdf_rect(block.bbox, page_w, page_h)
                pix = page.get_pixmap(dpi=dpi, alpha=False, clip=clip)
                img = pix.tobytes("jpeg")
                asset_key = (
                    f"{asset_prefix.rstrip('/')}/35b/figures/"
                    f"p{blueprint.page_no:04d}_{block.id}.jpg"
                )
                url = upload_import_asset(asset_key, img, content_type="image/jpeg")
                content = block.content.model_copy(
                    update={"asset_key": asset_key, "asset_url": url}
                )
                blocks.append(block.model_copy(update={"content": content}))
                cropped_keys.add(key)
            except Exception as exc:
                logger.warning(
                    "35b figure crop failed page=%d block=%s: %s",
                    blueprint.page_no,
                    block.id,
                    exc,
                )
                blocks.append(block)
        return blueprint.model_copy(update={"blocks": blocks})
    finally:
        doc.close()
