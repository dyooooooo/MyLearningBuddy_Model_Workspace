"""TextbookProfile feature whitelist + MinerU layout_stats aggregation."""
from __future__ import annotations

import copy
import logging
from typing import Any

from app.services.ir.textbook_profile import load_textbook_profile

logger = logging.getLogger(__name__)

FEATURE_KEYS: frozenset[str] = frozenset(
    {
        "columns",
        "running_header_footer",
        "aside",
        "toc_style",
        "figure_policy",
        "math_density",
    }
)

_DEFAULT_FEATURES: dict[str, Any] = {
    "columns": "triple_slots",
    "running_header_footer": True,
    "aside": "both",
    "toc_style": "text_list",
    "figure_policy": "strict_label",
    "math_density": "high",
}

_DEFAULT_ZONES: dict[str, Any] = {
    "aside_left_x1_max": 280,
    "aside_right_x0_min": 680,
}

_ASIDE_TYPES = frozenset({"aside_text", "page_aside_text"})
_HEADER_TYPES = frozenset({"header"})
_FOOTER_TYPES = frozenset({"footer", "page_footnote", "page_number"})
_EQUATION_TYPES = frozenset({"equation"})
_TOC_HINT_TYPES = frozenset({"list", "title"})


def aggregate_features(layout_stats: dict[str, Any] | None) -> dict[str, Any]:
    """Derive feature flags from MinerU type histograms / layout_stats."""
    stats = layout_stats or {}
    hist: dict[str, int] = {}
    raw_hist = stats.get("type_histogram") or {}
    if isinstance(raw_hist, dict):
        for k, v in raw_hist.items():
            try:
                hist[str(k)] = int(v)
            except (TypeError, ValueError):
                continue

    def _count(*keys: str) -> int:
        return sum(hist.get(k, 0) for k in keys)

    aside_n = _count(*_ASIDE_TYPES)
    if stats.get("has_aside") or aside_n > 0:
        aside = "both"
    else:
        aside = "none"

    has_header = bool(stats.get("has_running_header")) or _count(*_HEADER_TYPES) > 0
    has_footer = bool(stats.get("has_footer")) or _count(*_FOOTER_TYPES) > 0
    running = has_header or has_footer

    eq_n = _count(*_EQUATION_TYPES)
    math_density = "high" if eq_n >= 8 else ("medium" if eq_n >= 2 else "low")

    # TOC-like books often have many list/title blocks relative to body text.
    list_n = _count(*_TOC_HINT_TYPES)
    text_n = _count("text")
    toc_style = "text_list" if list_n >= 3 or text_n >= 1 else "unknown"

    out: dict[str, Any] = {
        "columns": "triple_slots",
        "running_header_footer": running,
        "aside": aside,
        "toc_style": toc_style,
        "figure_policy": "strict_label",
        "math_density": math_density,
    }
    return {k: out[k] for k in FEATURE_KEYS if k in out}


def _default_profile(profile_id: str) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "refine_mode": "layout_only",
        "refine_parallel": 1,
        "zones": dict(_DEFAULT_ZONES),
        "features": dict(_DEFAULT_FEATURES),
        "pedagogy_patterns": {},
        "figure_group": {"y_tolerance": 35},
    }


def merge_runtime_profile(
    profile_id: str,
    layout_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load textbook profile and overlay aggregated MinerU feature flags."""
    try:
        profile = copy.deepcopy(load_textbook_profile(profile_id))
    except FileNotFoundError:
        logger.warning("textbook profile missing id=%s — using defaults", profile_id)
        profile = _default_profile(profile_id)

    base_features = {
        k: v
        for k, v in (profile.get("features") or {}).items()
        if k in FEATURE_KEYS
    }
    if not base_features:
        base_features = dict(_DEFAULT_FEATURES)

    aggregated = aggregate_features(layout_stats)
    # Profile JSON is the baseline; MinerU stats only flip detection-driven flags.
    merged = dict(base_features)
    if layout_stats:
        if "aside" in aggregated:
            merged["aside"] = aggregated["aside"]
        if "running_header_footer" in aggregated:
            merged["running_header_footer"] = aggregated["running_header_footer"]
        if "math_density" in aggregated:
            merged["math_density"] = aggregated["math_density"]

    profile["features"] = {k: merged[k] for k in FEATURE_KEYS if k in merged}
    profile.setdefault("zones", dict(_DEFAULT_ZONES))
    profile.setdefault("profile_id", profile_id)
    return profile
