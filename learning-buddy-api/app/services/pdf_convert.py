"""PDF → normalized Markdown conversion via the Adapter + IR parse bridge.

The ``engine`` argument selects which parsing adapter runs.  Callers supply
the engine explicitly; there is no automatic routing or silent fallback.

Legacy meta keys (``pdf_kind``, ``converter``, ``ocr_progress``) are
preserved so that the studio ImportWizard can continue to display progress
without requiring a frontend rewrite.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from app.services.parsing import ParserError, get_parse_service
from app.services.pdf_text_normalize import normalize_pdf_text

logger = logging.getLogger(__name__)


def convert_pdf_bytes(
    pdf_bytes: bytes,
    *,
    filename: str = "import.pdf",
    engine: str = "mineru_cloud",
    progress_cb: Callable[[dict], None] | None = None,
    page_done_cb: Callable[[int, str, int], None] | None = None,
    asset_prefix: str = "",
    page_limit: int | None = None,
    page_ranges: str | None = None,
) -> tuple[str, dict]:
    """Parse ``pdf_bytes`` with the selected engine and return (markdown, meta).

    Raises ``ParserError`` on failure — no silent fallback.
    ``page_done_cb(page_no, page_md, total_pages)`` fires after each page is OCR'd.
    ``page_ranges`` preferred; else ``page_limit`` → Cloud ``1-N``.
    """
    svc = get_parse_service()
    parsed = svc.parse(
        pdf_bytes,
        filename=filename,
        engine=engine,
        progress_cb=progress_cb,
        page_done_cb=page_done_cb,
        asset_prefix=asset_prefix,
        page_limit=page_limit,
        page_ranges=page_ranges,
    )

    md = normalize_pdf_text(parsed.markdown)

    meta = {
        **parsed.meta,
        "engine": parsed.engine,
        "converter": parsed.meta.get("converter", parsed.engine),
        "pdf_kind": parsed.meta.get("pdf_kind", "unknown"),
        "has_structure": parsed.has_structure,
        "char_count": len(md),
    }
    # An empty blocks list is valid MinerU output (all-blank segment) and must
    # reach the IR layer so blank placeholders can be generated.
    if parsed.blocks is not None:
        meta["raw_blocks"] = parsed.blocks
    if parsed.meta.get("vision_pages"):
        meta["vision_pages"] = parsed.meta["vision_pages"]

    if progress_cb:
        try:
            progress_cb({"stage": "convert_done", "char_count": len(md)})
        except Exception:
            pass

    return md, meta
