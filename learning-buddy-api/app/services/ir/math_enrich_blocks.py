"""Full-text math enrichment for DocumentBlock IR.

Runs ``normalize_plain`` (the same engine the 伴读 pipeline uses) over every
block that contains math — not just ``type=equation`` — and stores the resulting
segments on ``meta.text_segments`` so the renderer can emit valid LaTeX as
MathJax and keep invalid fragments visible as raw Unicode.
"""
from __future__ import annotations

import logging

from app.math.cjk_split import looks_like_math
from app.math.normalize import normalize_plain
from app.services.document_ir import DocumentBlock

logger = logging.getLogger(__name__)

MATH_ENRICH_VERSION = "math_enrich_v1"

# Block types whose text may carry inline math worth normalizing.
_TEXT_MATH_TYPES = frozenset({"paragraph", "list", "caption", "heading", "title"})


def block_needs_math_enrich(block: DocumentBlock) -> bool:
    """Decide whether a block's text should be run through normalize_plain."""
    if block.type == "equation" and (block.text or "").strip():
        return True
    if block.type not in _TEXT_MATH_TYPES:
        return False
    text = (block.text or "").strip()
    if not text:
        return False
    return "$" in text or looks_like_math(text)


def _segments_to_meta(segments) -> list[dict]:
    out: list[dict] = []
    for seg in segments:
        out.append(
            {
                "kind": seg.kind,
                "raw": seg.raw,
                "latex": seg.latex,
                "valid": bool(seg.valid),
            }
        )
    return out


def enrich_block_math(block: DocumentBlock) -> DocumentBlock:
    """Attach normalized math segments; for equations also validate latex text."""
    if not block_needs_math_enrich(block):
        return block

    raw = (block.text or "").strip()
    rich = normalize_plain(raw)
    segments = _segments_to_meta(rich.segments)
    meta = dict(block.meta or {})
    meta["text_segments"] = segments
    meta["math_enrich_version"] = MATH_ENRICH_VERSION

    if block.type == "equation":
        valid_latex = [s["latex"] for s in segments if s["kind"] == "math" and s["valid"] and s["latex"]]
        if valid_latex:
            meta["latex_validated"] = True
            return block.model_copy(update={"text": " ".join(valid_latex), "meta": meta})
        meta["latex_validated"] = False
        return block.model_copy(update={"meta": meta})

    return block.model_copy(update={"meta": meta})


def enrich_page_blocks_math(page_blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    return [enrich_block_math(b) for b in page_blocks]


def enrich_blocks_math(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Batch math enrich (used by full-document rebuild paths)."""
    return [enrich_block_math(b) for b in blocks]
