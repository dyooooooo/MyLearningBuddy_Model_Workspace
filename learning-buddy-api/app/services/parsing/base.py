"""Parser Protocol — the interface every engine adapter must satisfy."""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .types import ParsedDocument


class Parser(Protocol):
    """Structural protocol for document-parsing engine adapters.

    Adapters are stateless; a new instance is created per parse call in
    ParseService.  ``is_ready`` is checked before ``parse`` so callers get a
    clear error instead of an obscure import/runtime failure.
    """

    name: str

    def parse(
        self,
        pdf_bytes: bytes,
        filename: str,
        progress_cb: Callable[[dict], None] | None = None,
        *,
        page_done_cb: Callable[[int, str, int], None] | None = None,
        asset_prefix: str = "",
        page_limit: int | None = None,
        page_ranges: str | None = None,
    ) -> ParsedDocument:
        """Parse ``pdf_bytes`` and return a ``ParsedDocument``.

        ``progress_cb`` receives dict payloads compatible with
        ``job.meta_json`` progress fields (ocr_progress, converter, …).
        ``page_ranges`` (e.g. ``"21-40"``) preferred; else ``page_limit`` → ``"1-N"``.
        Raise ``ParserError`` on any failure.
        """
        ...

    def is_ready(self) -> bool:
        """Return True when the adapter can accept parse calls right now."""
        ...


__all__ = ["Parser"]
