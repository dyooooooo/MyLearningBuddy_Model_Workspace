"""Prompt Slot registry — code knows which facets each LLM call needs."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.services.editions.schema import PolicyFacet

PipelineLine = Literal["mineru", "35b"]


@dataclass(frozen=True)
class PromptSlot:
    id: str
    line: PipelineLine
    base_prompt: str  # filename without path, e.g. mineru_page_refine_v1.md
    facets: tuple[PolicyFacet, ...] = field(default_factory=tuple)
    description: str = ""


# Facet sets reused across slots.
_LAYOUT = ("layout_geometry", "zone_hints", "aside_policy", "running_chrome")
_CONTENT = ("content_correct", "math_density", "pedagogy_roles", "figure_policy")
_REBUILD = ("layout_rebuild", "zone_hints", "aside_policy", "running_chrome", "toc_style")
_JUDGE = ("unit_judge", "running_chrome", "aside_policy", "figure_policy")
_VISION = ("vision_layout", "zone_hints", "aside_policy", "toc_style", "figure_policy", "math_density")

SLOTS: dict[str, PromptSlot] = {
    "mineru.refine_layout": PromptSlot(
        id="mineru.refine_layout",
        line="mineru",
        base_prompt="mineru_page_refine_v2.md",
        facets=_LAYOUT + ("pedagogy_roles",),
        description="Layout-only refine",
    ),
    "mineru.refine_full": PromptSlot(
        id="mineru.refine_full",
        line="mineru",
        base_prompt="mineru_page_refine_v1.md",
        facets=_LAYOUT + _CONTENT,
        description="Full refine",
    ),
    "mineru.rerecognize": PromptSlot(
        id="mineru.rerecognize",
        line="mineru",
        base_prompt="mineru_page_rerecognize_v1.md",
        facets=_LAYOUT + _CONTENT,
        description="User page re-recognize",
    ),
    "mineru.content_correct": PromptSlot(
        id="mineru.content_correct",
        line="mineru",
        base_prompt="mineru_page_content_correct_v1.md",
        facets=_CONTENT + ("zone_hints",),
        description="Content-correct mode",
    ),
    "mineru.layout_rebuild": PromptSlot(
        id="mineru.layout_rebuild",
        line="mineru",
        base_prompt="mineru_page_layout_rebuild_v1.md",
        facets=_REBUILD,
        description="Structural rebuild units",
    ),
    "mineru.unit_judge": PromptSlot(
        id="mineru.unit_judge",
        line="mineru",
        base_prompt="mineru_page_unit_judge_v1.md",
        facets=_JUDGE,
        description="Fusion unit judge",
    ),
    "b35.vision_layout": PromptSlot(
        id="b35.vision_layout",
        line="35b",
        base_prompt="layout_blueprint_v6.md",
        facets=_VISION,
        description="35B page blueprint",
    ),
}

# Map refine_mode → slot id
REFINE_MODE_SLOT: dict[str, str] = {
    "layout_only": "mineru.refine_layout",
    "full": "mineru.refine_full",
    "rerecognize": "mineru.rerecognize",
    "content_correct": "mineru.content_correct",
}


def get_slot(slot_id: str) -> PromptSlot:
    if slot_id not in SLOTS:
        raise KeyError(f"unknown prompt slot: {slot_id}")
    return SLOTS[slot_id]


def slot_for_refine_mode(mode: str) -> PromptSlot:
    sid = REFINE_MODE_SLOT.get(mode, "mineru.refine_full")
    return get_slot(sid)
