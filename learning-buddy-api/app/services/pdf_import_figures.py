"""Attach textbook figures to page markdown — page snapshot + embedded PDF images."""
from __future__ import annotations

import logging

from app.services.import_assets import upload_import_asset

logger = logging.getLogger(__name__)


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


def append_page_figure_markdown(
    pdf_bytes: bytes,
    page_no: int,
    page_jpeg: bytes,
    *,
    asset_prefix: str,
) -> str:
    """Return markdown block with page scan + extracted embedded images."""
    if not asset_prefix:
        return ""

    parts: list[str] = []
    prefix = asset_prefix.rstrip("/")

    if page_jpeg:
        page_key = f"{prefix}/pages/p{page_no:04d}_scan.jpg"
        page_url = upload_import_asset(page_key, page_jpeg, content_type="image/jpeg")
        parts.append(f"### 第 {page_no} 页原图\n\n![第 {page_no} 页原图]({page_url})")

    try:
        import fitz
    except ImportError:
        return "\n\n".join(parts) if parts else ""

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        logger.debug("skip embedded image extraction page=%d: %s", page_no, exc)
        return "\n\n".join(parts) if parts else ""

    try:
        page = doc.load_page(page_no - 1)
        images = page.get_images(full=True) or []
        for idx, img in enumerate(images, start=1):
            xref = img[0]
            try:
                extracted = doc.extract_image(xref)
            except Exception as exc:
                logger.debug("skip embedded image page=%d idx=%d: %s", page_no, idx, exc)
                continue
            img_bytes = extracted.get("image")
            if not img_bytes:
                continue
            ext = _image_ext_from_filter(extracted.get("name", ""), extracted.get("ext", ""))
            img_key = f"{prefix}/pages/p{page_no:04d}_fig{idx:02d}.{ext}"
            img_url = upload_import_asset(
                img_key,
                img_bytes,
                content_type=_content_type_for_ext(ext),
            )
            parts.append(f"![第 {page_no} 页插图 {idx}]({img_url})")
    finally:
        doc.close()

    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)
