"""Declarative layout decision layer.

Pipeline: ``features`` measures the page, ``rules`` supplies edition/page
policy as data, ``decide`` resolves zones with confidence and provenance.
Geometry stays in ``ir.column_detect``; nothing here emits HTML.
"""
from __future__ import annotations

from app.services.ir.layout.decide import decide_page
from app.services.ir.layout.features import (
    BlockFeatures,
    BlockInput,
    PageFeatures,
    extract_page_features,
)
from app.services.ir.layout.rules import (
    clear_rules_cache,
    load_ruleset,
    validate_ruleset,
)
from app.services.ir.layout.schema import (
    LayoutDecision,
    LayoutRule,
    PageDecision,
    RuleSet,
)
from app.services.ir.layout.xycut import xy_cut

__all__ = [
    "BlockFeatures",
    "BlockInput",
    "LayoutDecision",
    "LayoutRule",
    "PageDecision",
    "PageFeatures",
    "RuleSet",
    "clear_rules_cache",
    "decide_page",
    "extract_page_features",
    "load_ruleset",
    "validate_ruleset",
    "xy_cut",
]
