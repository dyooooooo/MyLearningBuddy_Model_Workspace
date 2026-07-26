"""Bridge IR (Intermediate Representation) for the document-parse layer.

Every parsing engine — Vision OCR, MinerU cloud — produces the same
ParsedDocument so consumers (pdf_convert, corpus_import) never branch on
which engine ran.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ParserError(RuntimeError):
    """Raised when a parse fails or when the engine is not ready.

    Carries a user-facing message; corpus_import surfaces it as
    job.status='failed' with job.error set to this message.
    """


@dataclass(frozen=True)
class ParsedDocument:
    """Engine-agnostic parse result.

    ``markdown`` is always present — every engine produces it.
    ``blocks`` is the MinerU ``content_list``-shaped structure: populated for
    mineru_cloud, None for vision_ocr.  Consumers that only need text read
    ``markdown``; consumers wanting multimodal structure prefer ``blocks`` and
    fall back to chunking ``markdown`` when it is absent.
    ``source_hash`` is reserved for future dedup/drift detection; not used yet.
    """

    markdown: str
    blocks: list[dict[str, Any]] | None = None
    engine: str = ""
    source_hash: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def has_structure(self) -> bool:
        return bool(self.blocks)


__all__ = ["ParsedDocument", "ParserError"]
