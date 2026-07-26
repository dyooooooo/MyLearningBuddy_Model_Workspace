"""Load Edition Bundles from disk (seed) or DB-published versions.

Shared read-only library used by MinerU and 35B lines independently —
callers must not import pipeline modules from here.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.editions.schema import EditionBundle, EditionConfig, EditionMeta, PolicyDocument

logger = logging.getLogger(__name__)

_EDITIONS_DIR = Path(__file__).resolve().parents[3] / "configs" / "editions"
_ALT_EDITIONS_DIR = Path(__file__).resolve().parents[4] / "configs" / "editions"
# Legacy flat profiles (migration shim).
_LEGACY_PROFILES = Path(__file__).resolve().parents[3] / "configs" / "textbook_profiles"
_ALT_LEGACY = Path(__file__).resolve().parents[4] / "configs" / "textbook_profiles"

# Hard-break: no silent default. Callers must pass edition_id explicitly
# after migration. Kept only for seed discovery / tests.
SEED_HUJIAO = "hujiao_math_hs_v1"
SEED_BASE = "_base"


def editions_dir() -> Path:
    if _EDITIONS_DIR.is_dir():
        return _EDITIONS_DIR
    return _ALT_EDITIONS_DIR


def list_seed_edition_ids() -> list[str]:
    root = editions_dir()
    if not root.is_dir():
        return []
    return sorted(
        p.name
        for p in root.iterdir()
        if p.is_dir() and (p / "config.json").is_file()
    )


def _legacy_profile_path(edition_id: str) -> Path | None:
    for root in (_LEGACY_PROFILES, _ALT_LEGACY):
        cand = root / f"{edition_id}.json"
        if cand.is_file():
            return cand
    return None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _bundle_from_legacy_profile(edition_id: str, data: dict[str, Any]) -> EditionBundle:
    """1:1 wrap of old textbook_profiles JSON into EditionBundle."""
    zones = data.get("zones") or {}
    fig = data.get("figure_group") or {}
    features = data.get("features") or {}
    geom = {
        "aside_left_x1_max": zones.get("aside_left_x1_max", 280),
        "aside_right_x0_min": zones.get("aside_right_x0_min", 680),
        "figure_y_tolerance": fig.get("y_tolerance", 35),
    }
    return EditionBundle(
        meta=EditionMeta(
            id=edition_id,
            version="1",
            display_name=data.get("display_name") or edition_id,
            base="_base",
        ),
        config=EditionConfig.model_validate(
            {
                "refine_mode": data.get("refine_mode", "skip"),
                "refine_parallel": data.get("refine_parallel", 1),
                "geometry": geom,
                "patterns": {
                    "pedagogy_patterns": data.get("pedagogy_patterns") or {},
                    "figure_caption": fig.get("caption_pattern"),
                },
                "features": features,
                "zones": zones,
                "figure_group": fig,
            }
        ),
        policy=PolicyDocument(version="1", entries=[]),
    )


def _load_seed_bundle(edition_id: str) -> EditionBundle:
    root = editions_dir() / edition_id
    if not root.is_dir():
        # Fall back to legacy flat profile during migration.
        legacy = _legacy_profile_path(edition_id)
        if legacy is not None:
            return _bundle_from_legacy_profile(edition_id, _load_json(legacy))
        raise FileNotFoundError(f"edition bundle not found: {edition_id} ({root})")

    meta_path = root / "meta.json"
    config_path = root / "config.json"
    policy_path = root / "policy.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"edition config missing: {config_path}")

    meta_raw = _load_json(meta_path) if meta_path.is_file() else {"id": edition_id}
    meta_raw.setdefault("id", edition_id)
    config_raw = _load_json(config_path)
    policy_raw = _load_json(policy_path) if policy_path.is_file() else {"version": "1", "entries": []}

    assets_raw: dict[str, Any] = {"theme_css": "theme.css"}
    assets_meta = root / "assets" / "assets.json"
    if assets_meta.is_file():
        assets_raw.update(_load_json(assets_meta))

    return EditionBundle.model_validate(
        {
            "meta": meta_raw,
            "config": config_raw,
            "policy": policy_raw,
            "assets": assets_raw,
        }
    )


@lru_cache(maxsize=32)
def load_bundle(edition_id: str, version: str | None = None) -> EditionBundle:
    """Load published/seed bundle. ``version`` reserved for DB-published pins."""
    if not edition_id or not str(edition_id).strip():
        raise ValueError("edition_id is required (hard-break: no implicit default)")
    eid = str(edition_id).strip()
    # DB-published version pin: try materialized path first.
    if version and version != "seed":
        pinned = editions_dir() / eid / "versions" / version
        if pinned.is_dir() and (pinned / "config.json").is_file():
            # Temporarily load from versioned dir by rewriting root via cache key
            # — use dedicated helper to avoid cache collision.
            return _load_from_dir(pinned, eid, version)
    return _load_seed_bundle(eid)


def _load_from_dir(root: Path, edition_id: str, version: str) -> EditionBundle:
    meta_raw = _load_json(root / "meta.json") if (root / "meta.json").is_file() else {"id": edition_id}
    meta_raw.setdefault("id", edition_id)
    meta_raw["version"] = version
    config_raw = _load_json(root / "config.json")
    policy_raw = (
        _load_json(root / "policy.json")
        if (root / "policy.json").is_file()
        else {"version": "1", "entries": []}
    )
    assets_raw: dict[str, Any] = {"theme_css": "theme.css"}
    return EditionBundle.model_validate(
        {
            "meta": meta_raw,
            "config": config_raw,
            "policy": policy_raw,
            "assets": assets_raw,
        }
    )


def clear_bundle_cache() -> None:
    load_bundle.cache_clear()


def theme_css_path(edition_id: str, version: str | None = None) -> Path:
    """Resolve theme CSS file for an edition."""
    bundle = load_bundle(edition_id, version)
    css_name = bundle.assets.theme_css or "theme.css"
    root = editions_dir() / edition_id
    cand = root / "assets" / css_name
    if cand.is_file():
        return cand
    # Legacy static fallback
    legacy = Path(__file__).resolve().parents[2] / "static" / "textbook-hujiao.css"
    if legacy.is_file():
        return legacy
    raise FileNotFoundError(f"theme css not found for edition {edition_id}")


def compile_patterns(bundle: EditionBundle) -> dict[str, Any]:
    """Compile regexes from bundle patterns for adapter use."""
    p = bundle.config.patterns
    out: dict[str, Any] = {
        "chapter": re.compile(p.chapter),
        "lesson": re.compile(p.lesson),
        "fig_label": re.compile(p.fig_label),
        "fig_ref": re.compile(p.fig_ref),
        "solution_lead": re.compile(p.solution_lead),
        "toc_trailing_page": re.compile(p.toc_trailing_page),
        "chapter_toc": re.compile(p.chapter_toc),
        "toc_section": re.compile(p.toc_section),
        "toc_summary": re.compile(p.toc_summary),
        "toc_titles": list(p.toc_titles),
        "pedagogy": {k: re.compile(v) for k, v in p.pedagogy_patterns.items()},
    }
    if p.figure_caption:
        out["figure_caption"] = re.compile(p.figure_caption)
    return out
