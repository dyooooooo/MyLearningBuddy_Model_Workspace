"""Job-scoped PDF render cache.

A single ``fitz.Document`` plus per-page JPEG / page-size caches so the refine
loop and layout enrichment do not reopen the PDF for every page. Safe to share
across a small thread pool: all PyMuPDF access is serialized with a lock because
``fitz.Document`` is not thread-safe.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = (596.0, 843.0)


class PdfRenderCache:
    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf_bytes = pdf_bytes or b""
        self._doc = None
        self._lock = threading.Lock()
        self._jpeg: dict[tuple[int, int], bytes] = {}
        self._size: dict[int, tuple[float, float]] = {}

    def _document(self):
        if self._doc is None:
            import fitz

            self._doc = fitz.open(stream=self._pdf_bytes, filetype="pdf")
        return self._doc

    def page_count(self) -> int:
        if not self._pdf_bytes:
            return 0
        doc = self._document()
        return int(len(doc))

    def page_jpeg(self, page_no: int, *, dpi: int = 150) -> bytes:
        """Render one page to JPEG bytes (1-based page_no), cached by (page, dpi)."""
        key = (page_no, dpi)
        cached = self._jpeg.get(key)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._jpeg.get(key)
            if cached is not None:
                return cached
            doc = self._document()
            page = doc.load_page(page_no - 1)
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            jpeg = pix.tobytes("jpeg")
            self._jpeg[key] = jpeg
            return jpeg

    def page_size(self, page_no: int) -> tuple[float, float]:
        """Return (width, height) in PDF points for a 1-based page_no."""
        if not self._pdf_bytes:
            return _DEFAULT_PAGE_SIZE
        cached = self._size.get(page_no)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._size.get(page_no)
            if cached is not None:
                return cached
            try:
                doc = self._document()
                page = doc.load_page(page_no - 1)
                size = (float(page.rect.width), float(page.rect.height))
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("page_size failed page=%d: %s", page_no, exc)
                size = _DEFAULT_PAGE_SIZE
            self._size[page_no] = size
            return size

    def close(self) -> None:
        with self._lock:
            if self._doc is not None:
                try:
                    self._doc.close()
                except Exception:
                    pass
                self._doc = None
            self._jpeg.clear()
            self._size.clear()

    def __enter__(self) -> "PdfRenderCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
