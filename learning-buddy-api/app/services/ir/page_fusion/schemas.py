"""Schemas for page unit fusion (layout_rebuild)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

UnitType = Literal[
    "paragraph", "heading", "list", "equation", "figure", "table", "caption", "title"
]
Zone = Literal["main", "aside_left", "aside_right", "full_bleed", "running"]
JudgeAction = Literal["keep_mineru", "keep_35b", "drop"]

_VALID_ZONES = frozenset({"main", "aside_left", "aside_right", "full_bleed", "running"})
_VALID_ACTIONS = frozenset({"keep_mineru", "keep_35b", "drop"})


class PageUnit(BaseModel):
    id: str
    type: str = "paragraph"
    zone: str = "main"
    pedagogy_role: str = "body"
    bbox: list[float] | None = None
    text: str = ""
    latex: str | None = None
    source: Literal["mineru", "35b"] = "mineru"
    # Optional original DocumentBlock fields when source=mineru
    block_id: str | None = None
    asset_url: str | None = None
    asset_key: str | None = None
    caption: str | None = None
    level: int | None = None
    order: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("zone", mode="before")
    @classmethod
    def coerce_zone(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        return s if s in _VALID_ZONES else "main"


class AlignedUnit(BaseModel):
    unit_id: str
    kind: Literal["paired", "mineru_only", "35b_only"]
    mineru: PageUnit | None = None
    b35: PageUnit | None = None


class JudgeDecision(BaseModel):
    unit_id: str
    action: str

    @field_validator("action", mode="before")
    @classmethod
    def coerce_action(cls, v: Any) -> str:
        s = str(v or "").strip().lower()
        return s if s in _VALID_ACTIONS else "drop"


class JudgeResult(BaseModel):
    page_no: int
    decisions: list[JudgeDecision] = Field(default_factory=list)


class RebuildUnitsResult(BaseModel):
    page_no: int
    units: list[PageUnit] = Field(default_factory=list)
