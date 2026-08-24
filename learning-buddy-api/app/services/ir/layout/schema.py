"""Data contracts for the declarative layout decision layer.

A layout decision is *data*: which zone a block lands in, which rule decided
it, how confident that rule is, and the evidence behind it. Keeping the
contract explicit is what makes a wrong page diff-able instead of only
visually wrong.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Zone = Literal["main", "aside_left", "aside_right", "full_bleed", "running"]

# Layer precedence: later layers win ties (page override beats edition beats base).
LAYER_ORDER = ("base", "edition", "page")


class LayoutRule(BaseModel):
    """One declarative layout rule.

    ``when`` is a boolean expression over the feature vocabulary exposed by
    :mod:`app.services.ir.layout.features`. It is evaluated with ``simpleeval``
    (no imports, no attribute access), so rule files stay safe to accept from
    the Studio agent.
    """

    id: str
    when: str
    zone: Zone | None = None
    role: str | None = None
    confidence: float = 0.8
    priority: int = 0
    # Restrict a rule to specific PDF pages (empty = all pages).
    pages: list[int] = Field(default_factory=list)
    note: str = ""
    enabled: bool = True
    # Filled by the loader, not by rule authors.
    layer: str = "base"
    source: str = ""

    @property
    def layer_rank(self) -> int:
        try:
            return LAYER_ORDER.index(self.layer)
        except ValueError:
            return 0

    def applies_to_page(self, page_no: int) -> bool:
        return not self.pages or int(page_no) in {int(p) for p in self.pages}


class RuleSet(BaseModel):
    version: int = 1
    # Below this score a block counts as low-confidence for Cursor escalate.
    escalate_below: float = 0.6
    # A page escalates when this share of its blocks are low confidence.
    escalate_ratio: float = 0.25
    rules: list[LayoutRule] = Field(default_factory=list)

    def for_page(self, page_no: int) -> list[LayoutRule]:
        return [r for r in self.rules if r.enabled and r.applies_to_page(page_no)]


class LayoutDecision(BaseModel):
    """Why one block ended up in one zone."""

    block_key: str
    zone: str
    confidence: float
    rule_id: str = "baseline"
    layer: str = "baseline"
    note: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < 0.6


class PageDecision(BaseModel):
    page_no: int
    decisions: dict[str, LayoutDecision] = Field(default_factory=dict)
    score: float = 1.0
    escalate: bool = False
    low_confidence_keys: list[str] = Field(default_factory=list)
