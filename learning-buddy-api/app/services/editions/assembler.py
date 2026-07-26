"""Assembler — filter Edition policy entries by PromptSlot facets → Markdown."""
from __future__ import annotations

from pathlib import Path

from app.services.editions.bundle import load_bundle
from app.services.editions.schema import EditionBundle, PolicyEntry, PolicyFacet
from app.services.editions.slots import PromptSlot, get_slot

_SEVERITY_ORDER = {"must": 0, "should": 1, "may": 2}

_MINERU_PROMPTS = Path(__file__).resolve().parents[3] / "data" / "prompts"
_B35_PROMPTS = Path(__file__).resolve().parents[2] / "import_35b" / "prompts"


def _base_prompt_path(slot: PromptSlot) -> Path:
    if slot.line == "35b":
        return _B35_PROMPTS / slot.base_prompt
    return _MINERU_PROMPTS / slot.base_prompt


def load_base_prompt(slot: PromptSlot) -> str:
    path = _base_prompt_path(slot)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    # Fallbacks for refine_v2 → v1
    if slot.base_prompt == "mineru_page_refine_v2.md":
        alt = _MINERU_PROMPTS / "mineru_page_refine_v1.md"
        if alt.is_file():
            return alt.read_text(encoding="utf-8")
    return ""


def filter_entries(
    bundle: EditionBundle,
    facets: tuple[PolicyFacet, ...] | list[PolicyFacet],
) -> list[PolicyEntry]:
    wanted = set(facets)
    entries = [e for e in bundle.policy.entries if e.facet in wanted]
    entries.sort(key=lambda e: (_SEVERITY_ORDER.get(e.severity, 9), e.id))
    return entries


def render_policy_markdown(entries: list[PolicyEntry], *, edition_id: str) -> str:
    if not entries:
        return ""
    lines = [
        "",
        "## Edition policy",
        f"edition_id={edition_id}",
        "",
    ]
    current_facet: str | None = None
    for e in entries:
        if e.facet != current_facet:
            current_facet = e.facet
            lines.append(f"### {current_facet}")
        prefix = {"must": "[MUST]", "should": "[SHOULD]", "may": "[MAY]"}.get(
            e.severity, "[SHOULD]"
        )
        lines.append(f"- {prefix} {e.statement}")
    lines.append("")
    return "\n".join(lines)


def assemble_prompt(
    slot_id: str,
    edition_id: str,
    *,
    version: str | None = None,
    extra_prefix: str = "",
    extra_suffix: str = "",
) -> str:
    """Load base prompt + inject filtered policy for the slot."""
    slot = get_slot(slot_id)
    bundle = load_bundle(edition_id, version)
    base = load_base_prompt(slot)
    entries = filter_entries(bundle, slot.facets)
    policy_md = render_policy_markdown(entries, edition_id=bundle.meta.id)
    # Also inject zone geometry as structured hints (from config, not policy).
    geom_hint = _geometry_hint(bundle)
    parts = [base]
    if extra_prefix:
        parts.append(extra_prefix)
    if geom_hint:
        parts.append(geom_hint)
    if policy_md:
        parts.append(policy_md)
    if extra_suffix:
        parts.append(extra_suffix)
    return "\n".join(parts)


def _geometry_hint(bundle: EditionBundle) -> str:
    zones = bundle.config.zones or {}
    geom = bundle.config.geometry
    left = zones.get("aside_left_x1_max", geom.aside_left_x1_max)
    right = zones.get("aside_right_x0_min", geom.aside_right_x0_min)
    return (
        "\n\n## Profile hints\n"
        f"profile_id={bundle.meta.id}\n"
        f"aside_right_x0_min≈{right}\n"
        f"aside_left_x1_max≈{left}\n"
    )
