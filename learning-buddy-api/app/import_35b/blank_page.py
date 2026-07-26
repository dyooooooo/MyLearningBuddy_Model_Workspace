"""Blank PDF page detection and placeholder blueprint/HTML for import pipeline."""
from __future__ import annotations

from app.import_35b.page_compiler import compile_page_html
from app.import_35b.raster import PdfRasterCache
from app.import_35b.schemas import PageBlueprint

# Fully white rasterized pages compress to very small JPEGs at DEFAULT_DPI.
# Used only as a secondary signal when the VLM returns ``blocks: []``.
_BLANK_JPEG_MAX_BYTES = 60_000


def is_blank_pdf_page(cache: PdfRasterCache, page_no: int) -> bool:
    """Return True when the PDF page has no extractable text or embedded images."""
    with cache._lock:  # noqa: SLF001 — reuse open document
        page = cache._document().load_page(page_no - 1)
        if page.get_text().strip():
            return False
        if page.get_images():
            return False
    return True


def is_empty_blocks_validation_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    msg = str(exc)
    return "blocks" in msg and "too_short" in msg


def should_fallback_blank_page(
    cache: PdfRasterCache,
    page_no: int,
    *,
    dpi: int,
    err: BaseException | None = None,
) -> bool:
    """True when a failed VLM response likely reflects a blank source page."""
    if is_blank_pdf_page(cache, page_no):
        return True
    if is_empty_blocks_validation_error(err):
        return len(cache.page_jpeg(page_no, dpi=dpi)) <= _BLANK_JPEG_MAX_BYTES
    return False


def blank_page_blueprint(page_no: int, *, page_size: list[int] | None = None) -> PageBlueprint:
    """Minimal valid blueprint for a blank page (empty main column)."""
    size = page_size or [1000, 1000]
    return PageBlueprint.model_validate(
        {
            "page_no": page_no,
            "page_size": size,
            "blocks": [
                {
                    "id": f"p{page_no:04d}_blank",
                    "kind": "text",
                    "content_type": "text",
                    "zone": "main",
                    "role": "body",
                    "bbox": [0, 0, size[0], size[1]],
                    "content": {"text": {"paragraphs": []}},
                }
            ],
        }
    )


def compile_blank_page_html(
    page_no: int,
    *,
    profile_id: str = "hujiao_math_hs_v1",
    page_size: list[int] | None = None,
) -> tuple[PageBlueprint, str]:
    """Build placeholder blueprint + grid HTML for a blank page."""
    bp = blank_page_blueprint(page_no, page_size=page_size)
    html = compile_page_html(bp, profile_id=profile_id)
    html = html.replace(
        '<article class="lb-page"',
        '<article class="lb-page lb-page--blank" data-blank="true"',
        1,
    )
    return bp, html
