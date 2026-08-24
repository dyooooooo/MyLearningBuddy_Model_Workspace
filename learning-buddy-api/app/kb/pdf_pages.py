"""Render PDF pages to JPEG with PyMuPDF. Knowledge-base only."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class KbPdfPages:
    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes or b""
        self._doc: Any = None

    def __enter__(self) -> KbPdfPages:
        import fitz

        self._doc = fitz.open(stream=self._pdf_bytes, filetype="pdf")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None

    def page_count(self) -> int:
        if self._doc is None:
            return 0
        return int(len(self._doc))

    def page_jpeg(self, page_no: int, *, dpi: int = 120) -> bytes:
        if self._doc is None:
            return b""
        page = self._doc.load_page(page_no - 1)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("jpeg")


@contextmanager
def open_pdf(pdf_bytes: bytes) -> Iterator[KbPdfPages]:
    pages = KbPdfPages(pdf_bytes)
    with pages as opened:
        yield opened
