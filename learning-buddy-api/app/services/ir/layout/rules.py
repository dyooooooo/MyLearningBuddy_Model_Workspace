"""Layout rules as data: load, cascade, evaluate.

Three layers merge like a stylesheet — ``base`` (shipped defaults) is overridden
by ``edition`` (per textbook) which is overridden by ``page`` (per-page fixes).
Within a layer, higher ``priority`` wins; across layers, the later layer wins a
tie. That gives a predictable resolution order without a rules engine.

Rule conditions are ``simpleeval`` expressions over the feature vocabulary in
:mod:`app.services.ir.layout.features`. They cannot import, call arbitrary
code, or reach attributes, so rule files are safe to accept from the Studio
agent rather than requiring Python edits.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf
from simpleeval import EvalWithCompoundTypes

from app.services.editions.geometry import ColumnGeometry
from app.services.ir.layout.schema import LayoutRule, RuleSet

logger = logging.getLogger(__name__)

_API_ROOT = Path(__file__).resolve().parents[4]
_BASE_RULES = _API_ROOT / "configs" / "layout" / "base_rules.yaml"
_EDITIONS = _API_ROOT / "configs" / "editions"


def base_rules_path() -> Path:
    return _BASE_RULES


def edition_rules_path(edition_id: str) -> Path:
    return _EDITIONS / edition_id / "layout_rules.yaml"


def page_override_path(edition_id: str, page_no: int) -> Path:
    return _EDITIONS / edition_id / "overrides" / "pages" / f"{int(page_no):04d}.yaml"


def _rule_expr_functions() -> dict[str, Any]:
    return {
        "startswith": lambda s, p: str(s).startswith(p),
        "endswith": lambda s, p: str(s).endswith(p),
        "contains": lambda s, p: str(p) in str(s),
        "matches": lambda s, p: bool(re.search(str(p), str(s))),
        "len": len,
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
    }


def rule_expr_functions() -> dict[str, Any]:
    """Expression helpers available in rule files.

    Exposed so the symptom checks in ``configs/checks/`` share one vocabulary
    with layout rules — an author should not have to remember which of two
    dialects a file is written in.
    """
    return _rule_expr_functions()


def _load_layer(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        conf = OmegaConf.load(path)
    except Exception as exc:  # noqa: BLE001 - a broken rule file must not kill an import
        logger.warning("layout rules: cannot parse %s: %s", path, exc)
        return None
    data = OmegaConf.to_container(conf, resolve=False)
    return data if isinstance(data, dict) else None


def _resolve_vars(data: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    """Resolve ``${wide_min}``-style references against edition geometry."""
    merged = OmegaConf.merge(OmegaConf.create(dict(variables)), OmegaConf.create(data))
    try:
        resolved = OmegaConf.to_container(merged, resolve=True)
    except Exception as exc:  # noqa: BLE001 - unresolved interpolation
        logger.warning("layout rules: interpolation failed (%s); using raw values", exc)
        resolved = OmegaConf.to_container(merged, resolve=False)
    return resolved if isinstance(resolved, dict) else {}


def _rules_from_layer(
    data: dict[str, Any],
    *,
    layer: str,
    source: str,
) -> list[LayoutRule]:
    out: list[LayoutRule] = []
    for raw in data.get("rules") or []:
        if not isinstance(raw, dict):
            continue
        try:
            rule = LayoutRule.model_validate({**raw, "layer": layer, "source": source})
        except Exception as exc:  # noqa: BLE001 - skip one bad rule, keep the rest
            logger.warning("layout rules: invalid rule in %s: %s", source, exc)
            continue
        out.append(rule)
    return out


def _layer_paths(edition_id: str | None, page_no: int) -> list[tuple[str, Path]]:
    layers: list[tuple[str, Path]] = [("base", _BASE_RULES)]
    if edition_id:
        layers.append(("edition", edition_rules_path(edition_id)))
        if page_no:
            layers.append(("page", page_override_path(edition_id, page_no)))
    return layers


def _mtime_stamp(paths: list[tuple[str, Path]]) -> tuple[float, ...]:
    out: list[float] = []
    for _, path in paths:
        try:
            out.append(path.stat().st_mtime)
        except OSError:
            out.append(-1.0)
    return tuple(out)


@lru_cache(maxsize=256)
def _load_ruleset_cached(
    edition_id: str | None,
    page_no: int,
    geometry: ColumnGeometry,
    _stamp: tuple[float, ...],
) -> RuleSet:
    variables: dict[str, Any] = {k: v for k, v in asdict(geometry).items()}

    settings: dict[str, Any] = {}
    rules: list[LayoutRule] = []
    for layer, path in _layer_paths(edition_id, page_no):
        data = _load_layer(path)
        if data is None:
            continue
        data = _resolve_vars(data, variables)
        for key in ("version", "escalate_below", "escalate_ratio"):
            if key in data:
                settings[key] = data[key]
        rules.extend(_rules_from_layer(data, layer=layer, source=str(path)))

    return RuleSet(rules=rules, **settings)


def load_ruleset(
    edition_id: str | None,
    page_no: int = 0,
    geometry: ColumnGeometry | None = None,
) -> RuleSet:
    """Merged base + edition + page ruleset, with geometry interpolation.

    Edition rule files are bind-mounted into the container so an operator can
    tune layout without a rebuild; the cache key therefore carries the layer
    mtimes and a saved file takes effect on the next page.
    """
    geom = geometry or ColumnGeometry()
    page = int(page_no or 0)
    stamp = _mtime_stamp(_layer_paths(edition_id, page))
    return _load_ruleset_cached(edition_id, page, geom, stamp)


def clear_rules_cache() -> None:
    _load_ruleset_cached.cache_clear()


class RuleEvaluator:
    """Evaluates rule conditions against one page's features."""

    def __init__(self, page_names: dict[str, Any]) -> None:
        self._page_names = dict(page_names)
        self._eval = EvalWithCompoundTypes(functions=_rule_expr_functions())
        self._failed: set[str] = set()

    def matches(self, rule: LayoutRule, block_names: dict[str, Any]) -> bool:
        self._eval.names = {**self._page_names, **block_names}
        try:
            return bool(self._eval.eval(rule.when))
        except Exception as exc:  # noqa: BLE001 - a bad expression must not stop rendering
            if rule.id not in self._failed:
                self._failed.add(rule.id)
                logger.warning(
                    "layout rule %s (%s) failed to evaluate: %s", rule.id, rule.source, exc
                )
            return False

    @property
    def failed_rule_ids(self) -> set[str]:
        return set(self._failed)


def validate_ruleset(ruleset: RuleSet, sample_names: dict[str, Any]) -> list[str]:
    """Return human-readable errors for rules that cannot evaluate.

    Used by the harness so a rule patch fails review instead of silently
    never matching in production.
    """
    evaluator = EvalWithCompoundTypes(
        names=dict(sample_names), functions=_rule_expr_functions()
    )
    errors: list[str] = []
    for rule in ruleset.rules:
        try:
            evaluator.eval(rule.when)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rule.id}: {exc}")
    return errors
