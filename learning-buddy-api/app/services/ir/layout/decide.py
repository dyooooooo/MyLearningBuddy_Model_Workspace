"""Turn page features into zone decisions, with confidence and provenance.

The geometric baseline answers first and states how sure it is. Rules from the
edition bundle may then override it. Whatever wins records ``rule_id`` and
``evidence`` so a wrong page can be explained instead of only re-tuned.

Pages the baseline is unsure about are queued for Cursor SDK
(``layout_escalate_pages``) — see :func:`decide_page`'s ``escalate`` flag.
Import never escalates to 35B refine.
"""
from __future__ import annotations

from typing import Any

from app.services.editions.geometry import ColumnGeometry
from app.services.ir.layout.features import BlockFeatures, PageFeatures
from app.services.ir.layout.rules import RuleEvaluator, load_ruleset
from app.services.ir.layout.schema import LayoutDecision, LayoutRule, PageDecision, RuleSet

# Confidence tiers for the geometric baseline.
_CONF_NO_BBOX = 0.35
_CONF_WIDE_MAIN = 0.95
_CONF_ASIDE_STRONG = 0.90
_CONF_ASIDE_WEAK = 0.70
_CONF_MAIN_PLAIN = 0.85
_CONF_MAIN_SUSPECT = 0.45


def _baseline_confidence(f: BlockFeatures) -> tuple[float, dict[str, Any]]:
    """How much to trust the geometric zone for this block."""
    if not f.has_bbox:
        return _CONF_NO_BBOX, {"reason": "no_bbox"}

    if f.is_full_width or f.is_wide:
        return _CONF_WIDE_MAIN, {"reason": "wide_block", "width": round(f.width, 1)}

    if f.baseline_zone == "aside_left":
        strong = f.has_companion_right and f.beyond_left_edge
        return (
            _CONF_ASIDE_STRONG if strong else _CONF_ASIDE_WEAK,
            {
                "reason": "aside_left",
                "companion_right": f.has_companion_right,
                "beyond_left_edge": f.beyond_left_edge,
            },
        )

    if f.baseline_zone == "aside_right":
        strong = f.has_companion_left and f.beyond_right_edge
        return (
            _CONF_ASIDE_STRONG if strong else _CONF_ASIDE_WEAK,
            {
                "reason": "aside_right",
                "companion_left": f.has_companion_left,
                "beyond_right_edge": f.beyond_right_edge,
            },
        )

    # Narrow block parked in main that looks like a margin note the band
    # detector missed — exactly the case worth escalating.
    looks_like_right_note = f.beyond_right_edge and f.has_companion_left
    looks_like_left_note = f.beyond_left_edge and f.has_companion_right
    if looks_like_right_note or looks_like_left_note:
        return (
            _CONF_MAIN_SUSPECT,
            {
                "reason": "main_but_margin_like",
                "beyond_left_edge": f.beyond_left_edge,
                "beyond_right_edge": f.beyond_right_edge,
                "companion_left": f.has_companion_left,
                "companion_right": f.has_companion_right,
            },
        )

    return _CONF_MAIN_PLAIN, {"reason": "main"}


def _best_rule(
    rules: list[LayoutRule],
    evaluator: RuleEvaluator,
    block_names: dict[str, Any],
) -> LayoutRule | None:
    best: LayoutRule | None = None
    best_key: tuple[int, int, int] | None = None
    for idx, rule in enumerate(rules):
        if not evaluator.matches(rule, block_names):
            continue
        key = (rule.priority, rule.layer_rank, idx)
        if best_key is None or key > best_key:
            best, best_key = rule, key
    return best


def decide_page(
    page: PageFeatures,
    *,
    edition_id: str | None = None,
    ruleset: RuleSet | None = None,
) -> PageDecision:
    """Resolve every block's zone, then flag the page if the result is shaky."""
    rs = ruleset or load_ruleset(
        edition_id, page.page_no, page.geometry or ColumnGeometry()
    )
    rules = rs.for_page(page.page_no)
    evaluator = RuleEvaluator(page.as_names())

    decisions: dict[str, LayoutDecision] = {}
    scores: list[float] = []
    low_keys: list[str] = []

    for f in page.blocks:
        confidence, evidence = _baseline_confidence(f)
        zone = f.baseline_zone
        rule_id, layer, note = "baseline", "baseline", ""

        rule = _best_rule(rules, evaluator, f.as_names()) if rules else None
        if rule is not None:
            rule_id, layer, note = rule.id, rule.layer, rule.note
            if rule.zone:
                zone = rule.zone
            confidence = float(rule.confidence)
            evidence = {**evidence, "rule": rule.id, "matched": rule.when}

        decisions[f.key] = LayoutDecision(
            block_key=f.key,
            zone=zone,
            confidence=confidence,
            rule_id=rule_id,
            layer=layer,
            note=note,
            evidence=evidence,
        )
        # Blocks without a bbox count too — a page that lost its geometry is
        # exactly the kind the vision model should look at.
        scores.append(confidence)
        if confidence < rs.escalate_below:
            low_keys.append(f.key)

    score = sum(scores) / len(scores) if scores else 1.0
    ratio = len(low_keys) / len(scores) if scores else 0.0
    return PageDecision(
        page_no=page.page_no,
        decisions=decisions,
        score=round(score, 4),
        escalate=bool(low_keys) and ratio >= rs.escalate_ratio,
        low_confidence_keys=low_keys,
    )
