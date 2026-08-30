"""Cut the first N pages so MinerU only has to read the cover and TOC."""

from __future__ import annotations


def slice_pdf_pages(pdf_bytes: bytes, max_pages: int = 3) -> bytes:
    import fitz

    raw = pdf_bytes or b""
    if not raw:
        raise ValueError("PDF empty")
    src = fitz.open(stream=raw, filetype="pdf")
    try:
        total = int(src.page_count)
        if total < 1:
            raise ValueError("PDF has no pages")
        keep = min(max(int(max_pages or 3), 1), total)
        if keep >= total:
            return raw
        dest = fitz.open()
        try:
            dest.insert_pdf(src, from_page=0, to_page=keep - 1)
            return dest.tobytes()
        finally:
            dest.close()
    finally:
        src.close()
