"""Render PDF pages to JPEG with PyMuPDF. Knowledge-base only."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# Workspace page previews (images/pNNNN.jpg). Prefer the Agent-owned
# constants in workspace/tools/figure_recrop.py when that file is mounted.
PAGE_JPEG_DPI = 150
PAGE_JPEG_QUALITY = 92


def _page_jpeg_defaults() -> tuple[int, int]:
    try:
        from app.kb.workspace_figures import page_jpeg_settings

        got = page_jpeg_settings()
    except Exception:
        got = None
    if got:
        return got
    return PAGE_JPEG_DPI, PAGE_JPEG_QUALITY


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

    def page_jpeg(
        self,
        page_no: int,
        *,
        dpi: int | None = None,
        quality: int | None = None,
    ) -> bytes:
        if self._doc is None:
            return b""
        if dpi is None or quality is None:
            wdpi, wq = _page_jpeg_defaults()
            dpi = wdpi if dpi is None else dpi
            quality = wq if quality is None else quality
        page = self._doc.load_page(page_no - 1)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        return pix.tobytes("jpeg", jpg_quality=quality)


@contextmanager
def open_pdf(pdf_bytes: bytes) -> Iterator[KbPdfPages]:
    pages = KbPdfPages(pdf_bytes)
    with pages as opened:
        yield opened
