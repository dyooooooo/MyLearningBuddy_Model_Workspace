"""PDF rasterization helpers for the 35B import experiment."""
from __future__ import annotations

import threading

DEFAULT_DPI = 170
DEFAULT_PAGE_SIZE = (596.0, 843.0)


class PdfRasterCache:
    """Job-scoped PyMuPDF document + page JPEG cache."""

    def __init__(self, pdf_bytes: bytes) -> None:
        self.pdf_bytes = pdf_bytes or b""
        self._doc = None
        self._lock = threading.Lock()
        self._jpeg: dict[tuple[int, int], bytes] = {}
        self._size: dict[int, tuple[float, float]] = {}

    def _document(self):
        if self._doc is None:
            import fitz

            self._doc = fitz.open(stream=self.pdf_bytes, filetype="pdf")
        return self._doc

    def page_count(self) -> int:
        if not self.pdf_bytes:
            return 0
        with self._lock:
            return int(self._document().page_count)

    def page_size(self, page_no: int) -> tuple[float, float]:
        cached = self._size.get(page_no)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._size.get(page_no)
            if cached is not None:
                return cached
            page = self._document().load_page(page_no - 1)
            size = (float(page.rect.width), float(page.rect.height))
            self._size[page_no] = size
            return size

    def page_jpeg(self, page_no: int, *, dpi: int = DEFAULT_DPI) -> bytes:
        key = (page_no, dpi)
        cached = self._jpeg.get(key)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._jpeg.get(key)
            if cached is not None:
                return cached
            page = self._document().load_page(page_no - 1)
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            jpeg = pix.tobytes("jpeg")
            self._jpeg[key] = jpeg
            return jpeg

    def close(self) -> None:
        with self._lock:
            if self._doc is not None:
                self._doc.close()
                self._doc = None
            self._jpeg.clear()
            self._size.clear()

    def __enter__(self) -> "PdfRasterCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

