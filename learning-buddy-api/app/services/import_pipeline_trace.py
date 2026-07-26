"""Explicit pipeline phase markers for import job meta (Studio topology polling).

The import job is a single orchestrated pipeline in buddy-worker — not separate
microservices. Each phase writes ``pipeline_phase`` (+ optional sub-progress)
via ImportProgressWriter so the UI can render accurate topology without guessing
from coarse ``job.status`` alone.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PIPELINE_PHASE_MINIO = "minio"
PIPELINE_PHASE_MINERU = "mineru_parse"
# Engine-specific parse phases — each line owns its own state, no longer
# borrowing the MinerU phase. See ENGINE_PHASES below.
PIPELINE_PHASE_VISION_OCR = "vision_ocr_parse"
PIPELINE_PHASE_LAYOUT_35B = "layout_35b"
PIPELINE_PHASE_ADAPTER = "adapter"
PIPELINE_PHASE_REFINE = "refine_35b"
PIPELINE_PHASE_RENDER = "render_html"
PIPELINE_PHASE_TREE = "corpus_tree"
PIPELINE_PHASE_DONE = "done"

# Legacy global order (kept for back-compat with old jobs / generic helpers).
PHASE_ORDER: tuple[str, ...] = (
    PIPELINE_PHASE_MINIO,
    PIPELINE_PHASE_MINERU,
    PIPELINE_PHASE_ADAPTER,
    PIPELINE_PHASE_REFINE,
    PIPELINE_PHASE_RENDER,
    PIPELINE_PHASE_TREE,
    PIPELINE_PHASE_DONE,
)

# ---------------------------------------------------------------------------
# Per-engine state machines — each of the three import lines is fully isolated
# with its OWN ordered phase list. The frontend mirrors this registry so each
# line renders its own topology instead of the MinerU one.
# ---------------------------------------------------------------------------

ENGINE_PHASES: dict[str, tuple[str, ...]] = {
    "mineru_cloud": (
        PIPELINE_PHASE_MINIO,
        PIPELINE_PHASE_MINERU,
        PIPELINE_PHASE_ADAPTER,
        PIPELINE_PHASE_REFINE,
        PIPELINE_PHASE_RENDER,
        PIPELINE_PHASE_TREE,
        PIPELINE_PHASE_DONE,
    ),
    "vision_ocr": (
        PIPELINE_PHASE_MINIO,
        PIPELINE_PHASE_VISION_OCR,
        PIPELINE_PHASE_ADAPTER,
        PIPELINE_PHASE_REFINE,
        PIPELINE_PHASE_RENDER,
        PIPELINE_PHASE_TREE,
        PIPELINE_PHASE_DONE,
    ),
    "vision_layout_35b": (
        PIPELINE_PHASE_MINIO,
        PIPELINE_PHASE_LAYOUT_35B,
        PIPELINE_PHASE_TREE,
        PIPELINE_PHASE_DONE,
    ),
}

# The parse phase each engine writes while reading the PDF (replaces the old
# behavior of forcing every engine onto ``mineru_parse``).
_ENGINE_PARSE_PHASE: dict[str, str] = {
    "mineru_cloud": PIPELINE_PHASE_MINERU,
    "vision_ocr": PIPELINE_PHASE_VISION_OCR,
    "vision_layout_35b": PIPELINE_PHASE_LAYOUT_35B,
}


def parse_phase_for_engine(engine: str) -> str:
    """Return the parse-phase marker for the given engine (default mineru)."""
    return _ENGINE_PARSE_PHASE.get(engine, PIPELINE_PHASE_MINERU)


def phases_for_engine(engine: str) -> tuple[str, ...]:
    return ENGINE_PHASES.get(engine, ENGINE_PHASES["mineru_cloud"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pipeline_patch(phase: str, **extra: Any) -> dict[str, Any]:
    return {"pipeline_phase": phase, "pipeline_phase_at": _now_iso(), **extra}
