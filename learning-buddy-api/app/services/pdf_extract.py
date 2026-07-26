from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SCANNED_CHARS_PER_PAGE = 80


@dataclass
class PdfPageText:
    page_no: int
    text: str


@dataclass
class PdfExtractResult:
    pages: list[PdfPageText]
    full_text: str
    meta: dict


def extract_pdf_bytes(pdf_bytes: bytes, *, filename: str = "import.pdf") -> PdfExtractResult:
    """Extract per-page text with PyMuPDF."""
    try:
        import fitz
    except ImportError as e:
        raise RuntimeError("pymupdf not installed; pip install pymupdf") from e

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages: list[PdfPageText] = []
    try:
        for i in range(doc.page_count):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            pages.append(PdfPageText(page_no=i + 1, text=text))
    finally:
        doc.close()

    chars_per_page = [len(p.text.strip()) for p in pages]
    full_text = "\n\n".join(p.text for p in pages if p.text.strip())
    avg_cpp = (sum(chars_per_page) / len(chars_per_page)) if chars_per_page else 0.0
    pdf_kind = "scanned" if avg_cpp < SCANNED_CHARS_PER_PAGE else "digital"

    meta = {
        "extractor": "pymupdf",
        "filename": filename,
        "page_count": len(pages),
        "chars_per_page": chars_per_page,
        "avg_chars_per_page": round(avg_cpp, 1),
        "pdf_kind": pdf_kind,
        "char_count": len(full_text),
    }
    logger.info(
        "pdf extract filename=%s pages=%d avg_cpp=%.1f kind=%s",
        filename,
        len(pages),
        avg_cpp,
        pdf_kind,
    )
    return PdfExtractResult(pages=pages, full_text=full_text, meta=meta)


def render_page_pixmap(pdf_bytes: bytes, page_no: int, *, dpi: int = 180) -> bytes:
    """Render one PDF page to JPEG bytes (for scanned OCR). page_no is 1-based."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc.load_page(page_no - 1)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("jpeg")
    finally:
        doc.close()
