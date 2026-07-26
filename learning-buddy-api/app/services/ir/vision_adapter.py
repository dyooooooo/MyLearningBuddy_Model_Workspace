"""Vision OCR pages → unified DocumentBlock IR."""
from __future__ import annotations

import re
from typing import Any

from app.services.document_ir import (
    DocumentBlock,
    clean_vision_ocr_text,
    figure_reference_count,
    heading_level_from_text,
    is_chapter_text,
    is_lesson_text,
    normalize_figure_caption,
    stable_block_id,
    asset_url_from_key,
)
from app.services.import_assets import upload_import_asset

_HEADING_MD_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_EXAMPLE_RE = re.compile(r"^(例\s*[０-９0-9]+|练习\s*[\d.]+|复习题|内容提要)")
_FIG_REF_RE = re.compile(r"如图\s*([\d\.．-]+)|图\s*([\d\.．-]+)\s*所示")


def _split_ocr_paragraphs(page_text: str) -> list[tuple[str, str]]:
    """Split OCR markdown into (kind, text) segments."""
    segments: list[tuple[str, str]] = []
    buf: list[str] = []
    for line in page_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if buf:
                segments.append(("paragraph", "\n".join(buf).strip()))
                buf = []
            continue
        if _HEADING_MD_RE.match(stripped) or is_chapter_text(stripped) or is_lesson_text(stripped):
            if buf:
                segments.append(("paragraph", "\n".join(buf).strip()))
                buf = []
            segments.append(("heading", stripped))
            continue
        if _EXAMPLE_RE.match(stripped):
            if buf:
                segments.append(("paragraph", "\n".join(buf).strip()))
                buf = []
            segments.append(("example", stripped))
            continue
        buf.append(line)
    if buf:
        segments.append(("paragraph", "\n".join(buf).strip()))
    return [(k, t) for k, t in segments if t.strip()]


def _image_ext_from_filter(name: str, filt: str) -> str:
    ext = (name or "").split(".")[-1].lower()
    if ext in ("png", "jpg", "jpeg", "gif", "webp"):
        return ext
    if "jpeg" in (filt or "").lower() or "jpg" in (filt or "").lower():
        return "jpg"
    if "png" in (filt or "").lower():
        return "png"
    return "png"


def _content_type_for_ext(ext: str) -> str:
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "application/octet-stream")


def _extract_embedded_figures(
    *,
    pdf_bytes: bytes,
    page_no: int,
    asset_prefix: str,
    start_order: int,
) -> list[DocumentBlock]:
    """Extract embedded PDF images sorted by vertical position."""
    try:
        import fitz
    except ImportError:
        return []

    prefix = asset_prefix.rstrip("/")
    figures: list[tuple[float, DocumentBlock]] = []
    seen_xrefs: set[int] = set()
    order = start_order

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:
        return []

    try:
        page = doc.load_page(page_no - 1)
        images = page.get_images(full=True) or []
        for idx, img in enumerate(images, start=1):
            xref = img[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                extracted = doc.extract_image(xref)
            except Exception:
                continue
            img_bytes = extracted.get("image")
            if not img_bytes:
                continue
            rects = page.get_image_rects(xref) or []
            y0 = float(rects[0].y0) if rects else float(idx * 1000)
            width = float(rects[0].width) if rects else 0.0
            height = float(rects[0].height) if rects else 0.0
            if width > 0 and height > 0 and (width < 30 or height < 12):
                continue
            ext = _image_ext_from_filter(extracted.get("name", ""), extracted.get("ext", ""))
            img_key = f"{prefix}/pages/p{page_no:04d}_fig{idx:02d}.{ext}"
            img_url = upload_import_asset(
                img_key,
                img_bytes,
                content_type=_content_type_for_ext(ext),
            )
            order += 1
            bbox = None
            if rects:
                r = rects[0]
                bbox = [float(r.x0), float(r.y0), float(r.x1), float(r.y1)]
            figures.append(
                (
                    y0,
                    DocumentBlock(
                        id=stable_block_id("vision", str(page_no), f"fig{idx}"),
                        type="figure",
                        page_no=page_no,
                        order=order,
                        bbox=bbox,
                        asset_key=img_key,
                        asset_url=img_url,
                        caption=None,
                        meta={"figure_kind": "embedded", "engine": "vision_ocr"},
                    ),
                )
            )
    finally:
        doc.close()

    figures.sort(key=lambda item: item[0])
    return [block for _, block in figures]


def _inline_figures_after_references(
    text_blocks: list[DocumentBlock],
    figures: list[DocumentBlock],
) -> list[DocumentBlock]:
    """Insert extracted figures after paragraphs that reference them."""
    if not figures:
        return text_blocks

    anchor_indices: list[int] = []
    for i, block in enumerate(text_blocks):
        if block.text and _FIG_REF_RE.search(block.text):
            anchor_indices.append(i)

    if not anchor_indices:
        return text_blocks + figures

    per_anchor = max(1, len(figures) // len(anchor_indices))
    out: list[DocumentBlock] = []
    fig_i = 0
    anchor_set = set(anchor_indices)
    for i, block in enumerate(text_blocks):
        out.append(block)
        if i not in anchor_set or fig_i >= len(figures):
            continue
        remaining = len(figures) - fig_i
        remaining_anchors = len([a for a in anchor_indices if a >= i])
        n = min(per_anchor, max(1, remaining // remaining_anchors))
        n = min(n, remaining)
        out.extend(figures[fig_i : fig_i + n])
        fig_i += n

    if fig_i < len(figures):
        out.extend(figures[fig_i:])
    return out


def _text_blocks_from_ocr(
    page_text: str,
    *,
    page_no: int,
    start_order: int,
) -> tuple[list[DocumentBlock], int]:
    blocks: list[DocumentBlock] = []
    order = start_order
    for kind, text in _split_ocr_paragraphs(page_text):
        if kind == "heading":
            btype = "title" if is_chapter_text(text) else "heading"
            level = heading_level_from_text(text)
            order += 1
            blocks.append(
                DocumentBlock(
                    id=stable_block_id("vision", str(page_no), str(order), text[:40]),
                    type=btype,  # type: ignore[arg-type]
                    page_no=page_no,
                    order=order,
                    text=_HEADING_MD_RE.sub(r"\2", text) if _HEADING_MD_RE.match(text) else text,
                    level=level,
                    meta={"engine": "vision_ocr", "segment": kind},
                )
            )
        else:
            order += 1
            blocks.append(
                DocumentBlock(
                    id=stable_block_id("vision", str(page_no), str(order), text[:40]),
                    type="paragraph",  # type: ignore[arg-type]
                    page_no=page_no,
                    order=order,
                    text=text,
                    meta={"engine": "vision_ocr", "segment": kind, "atomic_example": kind == "example"},
                )
            )
    return blocks, order


def vision_page_to_ir(
    *,
    page_no: int,
    page_text: str,
    pdf_bytes: bytes,
    page_jpeg: bytes | None,
    asset_prefix: str,
    start_order: int = 0,
) -> tuple[list[DocumentBlock], int]:
    """Build IR blocks for one Vision-OCR page with inline figure placement."""
    cleaned = clean_vision_ocr_text(page_text)
    text_blocks, order = _text_blocks_from_ocr(cleaned, page_no=page_no, start_order=start_order)

    if not asset_prefix:
        return text_blocks, order

    figures = _extract_embedded_figures(
        pdf_bytes=pdf_bytes,
        page_no=page_no,
        asset_prefix=asset_prefix,
        start_order=order,
    )
    if figures:
        order = max(b.order for b in figures)
        merged = _inline_figures_after_references(text_blocks, figures)
        return merged, order

    if page_jpeg and figure_reference_count(cleaned) == 0:
        prefix = asset_prefix.rstrip("/")
        page_key = f"{prefix}/pages/p{page_no:04d}_scan.jpg"
        page_url = upload_import_asset(page_key, page_jpeg, content_type="image/jpeg")
        order += 1
        merged = text_blocks + [
            DocumentBlock(
                id=stable_block_id("vision", str(page_no), "scan"),
                type="figure",
                page_no=page_no,
                order=order,
                asset_key=page_key,
                asset_url=page_url,
                caption=f"第 {page_no} 页原图",
                meta={"figure_kind": "page_scan", "engine": "vision_ocr"},
            )
        ]
        return merged, order

    return text_blocks, order


def vision_pages_to_ir(
    pages: list[dict[str, Any]],
    *,
    pdf_bytes: bytes,
    asset_prefix: str,
) -> list[DocumentBlock]:
    """Build full document IR from collected vision page results."""
    all_blocks: list[DocumentBlock] = []
    order = 0
    for entry in pages:
        page_no = int(entry.get("page_no") or 1)
        text = str(entry.get("text") or "")
        jpeg = entry.get("page_jpeg")
        if not isinstance(jpeg, bytes) and pdf_bytes:
            try:
                from app.services.pdf_extract import render_page_pixmap

                jpeg = render_page_pixmap(pdf_bytes, page_no, dpi=180)
            except Exception:
                jpeg = None
        page_blocks, order = vision_page_to_ir(
            page_no=page_no,
            page_text=text,
            pdf_bytes=pdf_bytes,
            page_jpeg=jpeg if isinstance(jpeg, bytes) else None,
            asset_prefix=asset_prefix,
            start_order=order,
        )
        all_blocks.extend(page_blocks)
    return all_blocks


def resolve_asset_urls(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Fill asset_url from asset_key when missing."""
    out: list[DocumentBlock] = []
    for block in blocks:
        if block.asset_key and not block.asset_url:
            out.append(block.model_copy(update={"asset_url": asset_url_from_key(block.asset_key)}))
        else:
            out.append(block)
    return out
