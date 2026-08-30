from __future__ import annotations

import fitz

from app.kb.pdf_slice import slice_pdf_pages


def _n_page_pdf(n: int) -> bytes:
    doc = fitz.open()
    for i in range(n):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def test_slice_keeps_first_ten() -> None:
    raw = _n_page_pdf(15)
    cut = slice_pdf_pages(raw, 10)
    out = fitz.open(stream=cut, filetype="pdf")
    try:
        assert out.page_count == 10
        assert "page 1" in out.load_page(0).get_text()
        assert "page 10" in out.load_page(9).get_text()
    finally:
        out.close()


def test_slice_short_pdf_unchanged() -> None:
    raw = _n_page_pdf(3)
    assert slice_pdf_pages(raw, 10) == raw
