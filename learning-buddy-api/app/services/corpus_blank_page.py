"""Blank PDF page detection + placeholder DocumentBlock for corpus (MinerU) line.

Uses shared ``PdfRenderCache`` (corpus services). Does NOT import ``app.import_35b``.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from app.services.document_ir import DocumentBlock, stable_block_id
from app.services.ir.pdf_render_cache import PdfRenderCache

logger = logging.getLogger(__name__)

_BLANK_JPEG_MAX_BYTES = 60_000


def parse_page_ranges(ranges: str | None) -> set[int]:
    """Expand a MinerU-style ranges string ("1-20", "1-3,63") to absolute page numbers."""
    pages: set[int] = set()
    for part in (ranges or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not m:
            continue
        start = int(m.group(1))
        end = int(m.group(2) or start)
        if end < start:
            start, end = end, start
        pages.update(range(start, end + 1))
    return pages


def is_blank_pdf_page(cache: PdfRenderCache, page_no: int) -> bool:
    """True when the PDF page has no extractable text or embedded images."""
    with cache._lock:  # noqa: SLF001 — reuse open document
        page = cache._document().load_page(page_no - 1)
        if page.get_text().strip():
            return False
        if page.get_images():
            return False
    return True


def looks_blank_by_jpeg(cache: PdfRenderCache, page_no: int, *, dpi: int = 72) -> bool:
    try:
        return len(cache.page_jpeg(page_no, dpi=dpi)) <= _BLANK_JPEG_MAX_BYTES
    except Exception:
        return False


def blank_placeholder_block(page_no: int) -> DocumentBlock:
    return DocumentBlock(
        id=stable_block_id("mineru_cloud", str(page_no), "blank"),
        type="paragraph",
        page_no=page_no,
        order=1,
        text="（空白页）",
        meta={"blank": True, "engine": "mineru_cloud"},
    )


def mark_blank_html(html: str) -> str:
    if 'data-blank="true"' in html:
        return html
    return html.replace(
        '<article class="lb-page"',
        '<article class="lb-page lb-page--blank" data-blank="true"',
        1,
    )


def ensure_blank_pages(
    blocks: list[DocumentBlock],
    *,
    pdf_bytes: bytes,
    total_pages: int | None = None,
    expected_pages: Iterable[int] | None = None,
    missing_pages: list[int] | None = None,
) -> list[DocumentBlock]:
    """Inject blank placeholders for PDF pages with no content blocks.

    ``expected_pages`` is the set of absolute page numbers this parse segment
    was supposed to cover (from ``page_ranges``/``page_limit``); gaps inside it
    (pages MinerU omitted entirely) get blank placeholders. When not given,
    ``total_pages`` fills ``range(1, total_pages + 1)`` — only meaningful for
    whole-book or first-segment imports.

    Pages whose IR is empty but whose PDF page is NOT blank (MinerU dropped
    real content) are logged and appended to ``missing_pages`` when provided.
    """
    if not pdf_bytes:
        return blocks

    by_page: dict[int, list[DocumentBlock]] = {}
    for b in blocks:
        by_page.setdefault(b.page_no, []).append(b)

    cache = PdfRenderCache(pdf_bytes)
    try:
        pdf_n = 0
        try:
            with cache._lock:  # noqa: SLF001
                pdf_n = int(cache._document().page_count)
        except Exception:
            pdf_n = 0
        target_pages = set(by_page.keys())
        if expected_pages:
            exp = set(expected_pages)
            if pdf_n > 0:
                exp = {p for p in exp if 1 <= p <= pdf_n}
            target_pages |= exp
        elif total_pages and total_pages > 0:
            target_pages |= set(range(1, int(total_pages) + 1))
        elif pdf_n > 0:
            # Only fill gaps within the span MinerU already produced,
            # plus detect empty pages that have blocks but no text.
            if by_page:
                target_pages |= set(range(min(by_page), max(by_page) + 1))

        out = list(blocks)
        for page_no in sorted(target_pages):
            page_blocks = by_page.get(page_no) or []
            content_len = sum(b.content_length() for b in page_blocks)
            if page_blocks and content_len > 0:
                continue
            if not is_blank_pdf_page(cache, page_no) and not looks_blank_by_jpeg(
                cache, page_no
            ):
                # Empty IR but not a blank PDF page — MinerU dropped real
                # content here. Surface it instead of silently skipping.
                if not page_blocks:
                    logger.warning(
                        "import page missing content page=%d (IR empty, PDF not blank)",
                        page_no,
                    )
                    if missing_pages is not None:
                        missing_pages.append(page_no)
                continue
            if any((b.meta or {}).get("blank") for b in page_blocks):
                continue
            # Replace empty page blocks with a single placeholder.
            out = [b for b in out if b.page_no != page_no]
            out.append(blank_placeholder_block(page_no))
        out.sort(key=lambda b: (b.page_no, b.order))
        return out
    finally:
        cache.close()
