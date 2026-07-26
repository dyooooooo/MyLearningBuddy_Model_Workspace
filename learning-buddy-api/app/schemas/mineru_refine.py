"""Pydantic models for MinerU page refine (35B) JSON contract."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

LayoutZone = Literal["main", "aside_left", "aside_right", "full_bleed", "running"]

# Canonical set; 35B may return type names (equation/heading) — coerced to body.
_VALID_ROLES = frozenset({
    "body", "running_header", "running_footer", "page_number",
    "example", "exercise", "review", "explore",
    "reading", "summary", "definition", "note", "prompt", "symbol_note",
    "think", "figure", "figure_caption", "toc",
})

_VALID_ZONES = frozenset({
    "main", "aside_left", "aside_right", "full_bleed", "running",
})

REFINE_PROMPT_VERSION = "mineru_page_refine_v2"
REFINE_PROMPT_VERSION_FULL = "mineru_page_refine_v1"
REFINE_PROMPT_VERSION_RERECOGNIZE = "mineru_page_rerecognize_v1"
REFINE_PROMPT_VERSION_CONTENT_CORRECT = "mineru_page_content_correct_v1"

# Map refine_mode -> prompt version.
# Book import default is profile ``skip``; user page re-ID uses ``rerecognize``.
REFINE_MODE_PROMPT = {
    "layout_only": REFINE_PROMPT_VERSION,
    "full": REFINE_PROMPT_VERSION_FULL,
    "rerecognize": REFINE_PROMPT_VERSION_RERECOGNIZE,
    "content_correct": REFINE_PROMPT_VERSION_CONTENT_CORRECT,
}


class RefineBlockPatch(BaseModel):
    id: str
    zone: str = "main"
    pedagogy_role: str = "body"
    text: str | None = None
    latex: str | None = None
    anchor_block_id: str | None = None
    figure_group_id: str | None = None

    @field_validator("zone", mode="before")
    @classmethod
    def coerce_zone(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        return s if s in _VALID_ZONES else "main"

    @field_validator("pedagogy_role", mode="before")
    @classmethod
    def coerce_role(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        return s if s in _VALID_ROLES else "body"


class RefineChunk(BaseModel):
    chunk_id: str
    label: str
    block_ids: list[str] = Field(min_length=1)
    anchor: str | None = None


class PageRefineResult(BaseModel):
    page_no: int = Field(ge=1)
    blocks: list[RefineBlockPatch] = Field(default_factory=list)
    chunks: list[RefineChunk] = Field(default_factory=list)


class RefineAllPagesResult(BaseModel):
    prompt_version: str = REFINE_PROMPT_VERSION
    pages: list[PageRefineResult] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    # Per-page 35B wall time (ms), keyed by page_no, for telemetry.
    refine_ms_by_page: dict[int, int] = Field(default_factory=dict)
