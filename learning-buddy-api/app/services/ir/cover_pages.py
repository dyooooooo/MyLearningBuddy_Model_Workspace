"""Cover / front-matter pages: show full-page scan, drop OCR text.

Textbook covers (and often the back cover) are designed as full-bleed artwork.
OCR text on those pages is noisy and not useful in the Studio preview — prefer
a single ``figure_kind=page_scan`` block rendered as ``full_bleed``.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.document_ir import DocumentBlock, stable_block_id
from app.services.import_assets import upload_import_asset
from app.services.ir.pdf_render_cache import PdfRenderCache

logger = logging.getLogger(__name__)

# Default candidates: front + back cover of a bound volume.
DEFAULT_COVER_PAGES = (1, 2)

# MinerU / layout bboxes after slim enrich are on a 0–1000 canvas.
_NORM_PAGE_W = 1000.0
_NORM_PAGE_H = 1000.0


def cover_pages_from_profile(profile: dict[str, Any] | None) -> list[int]:
    """Read ``features.cover_pages`` from an edition/profile dict."""
    feats = (profile or {}).get("features") or {}
    raw = feats.get("cover_pages")
    if raw is None:
        return list(DEFAULT_COVER_PAGES)
    out: list[int] = []
    for x in raw if isinstance(raw, (list, tuple)) else []:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out or list(DEFAULT_COVER_PAGES)


def _bbox_area_ratio(bbox: list[float] | None) -> float:
    if not bbox or len(bbox) < 4:
        return 0.0
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return area / (_NORM_PAGE_W * _NORM_PAGE_H)


def _pdf_page_has_embedded_images(render_cache: PdfRenderCache, page_no: int) -> bool:
    """True when the PDF page embeds at least one image XObject."""
    try:
        doc = render_cache._document()  # noqa: SLF001 — shared cache
        page = doc.load_page(page_no - 1)
        return bool(page.get_images(full=True))
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("cover image probe failed page=%d: %s", page_no, exc)
        return False


def looks_like_cover_page(
    page_no: int,
    blocks: list[DocumentBlock],
    *,
    profile: dict[str, Any] | None = None,
    render_cache: PdfRenderCache | None = None,
) -> bool:
    """Heuristic: configured early pages that carry artwork are covers.

    Rules (any match after page is in ``cover_pages``):
    - MinerU/IR already produced a figure (especially large / near full-bleed)
    - PDF page embeds images (designed cover without a MinerU crop)
    - Page 1 with sparse title-like text (front cover fallback)
    """
    cover_pages = set(cover_pages_from_profile(profile))
    if page_no not in cover_pages:
        return False

    figures = [
        b
        for b in blocks
        if b.type == "figure" and (b.asset_url or b.asset_key)
    ]
    if figures:
        max_ratio = max(_bbox_area_ratio(b.bbox) for b in figures)
        if max_ratio >= 0.12:
            return True
        # Any figure on a configured cover candidate is enough — covers often
        # crop a hero illustration rather than the full page.
        return True

    if render_cache is not None and _pdf_page_has_embedded_images(render_cache, page_no):
        return True

    # Front cover fallback: title / imprint noise only, no figure yet.
    if page_no == 1:
        textish = [
            b
            for b in blocks
            if b.type in ("paragraph", "heading", "title", "list")
            and (b.text or "").strip()
        ]
        return len(textish) <= 12

    return False


def materialize_cover_page(
    page_no: int,
    *,
    render_cache: PdfRenderCache,
    asset_prefix: str,
    dpi: int = 144,
) -> DocumentBlock:
    """Upload a full-page JPEG and return a single page_scan figure block."""
    prefix = (asset_prefix or "").rstrip("/")
    if not prefix:
        raise ValueError("asset_prefix required for cover page scan")

    jpeg = render_cache.page_jpeg(page_no, dpi=dpi)
    page_key = f"{prefix}/pages/p{page_no:04d}_cover.jpg"
    page_url = upload_import_asset(page_key, jpeg, content_type="image/jpeg")
    return DocumentBlock(
        id=stable_block_id("cover", str(page_no), "scan"),
        type="figure",
        page_no=page_no,
        order=1,
        bbox=[0.0, 0.0, _NORM_PAGE_W, _NORM_PAGE_H],
        asset_key=page_key,
        asset_url=page_url,
        caption="",
        meta={
            "figure_kind": "page_scan",
            "layout_zone": "full_bleed",
            "pedagogy_role": "cover",
            "cover_page": True,
            "engine": "pdf_cover_scan",
        },
    )


def collapse_cover_page_blocks(
    page_blocks: list[DocumentBlock],
    *,
    page_no: int,
    profile: dict[str, Any] | None,
    render_cache: PdfRenderCache | None,
    asset_prefix: str,
) -> list[DocumentBlock]:
    """If this page looks like a cover, replace IR with one full-bleed scan.

    OCR text blocks are dropped — covers are visual only.
    """
    if not page_blocks:
        return page_blocks
    if render_cache is None or not (asset_prefix or "").strip():
        return page_blocks
    if not looks_like_cover_page(
        page_no, page_blocks, profile=profile, render_cache=render_cache
    ):
        return page_blocks

    try:
        cover = materialize_cover_page(
            page_no, render_cache=render_cache, asset_prefix=asset_prefix
        )
    except Exception as exc:
        logger.warning("cover materialize failed page=%d: %s", page_no, exc)
        return page_blocks

    logger.info(
        "cover page collapsed page=%d blocks %d → 1 page_scan",
        page_no,
        len(page_blocks),
    )
    return [cover]
