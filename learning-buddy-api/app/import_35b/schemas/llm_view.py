"""LLM-view schema for 35B PageBlueprint guided decoding.

This module defines a *subset* of PageBlueprint that the model is allowed to
fill in.  Fields that are populated by the post-processing pipeline
(asset_key, asset_url, text_segments) are intentionally absent so the model
never hallucinates them.

Cross-field validation (bbox geometry, at least one main block, anchor
references, kind/zone alignment) is NOT replicated here — Pydantic's
PageBlueprint continues to own those rules.  This schema only constrains
shape and enum values during guided decoding.
"""
from __future__ import annotations

import copy
from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.import_35b.schemas.page_blueprint import BlockKind, Zone, _VALID_ROLES

# ── xgrammar-unsafe keywords to strip before sending to vLLM ──────────────
# NOTE: ``maxLength`` is intentionally NOT stripped. The current vLLM/xgrammar
# backend (0.22.x) both accepts and *enforces* string ``maxLength`` (verified:
# a value forced past the bound is truncated at the limit). Keeping it lets the
# guided decoder cap figure captions so the model cannot dump a whole text page
# into ``figure.caption``. ``minLength`` stays stripped — a lower bound can force
# the grammar into an unsatisfiable state on legitimately short/empty fields.
_STRIP_KEYWORDS = frozenset(
    {
        "minItems",
        "maxItems",
        "minLength",
        "format",
        "$comment",
        "exclusiveMinimum",
        "exclusiveMaximum",
    }
)

# ── LLM-view sub-models ────────────────────────────────────────────────────

_RoleEnum = Literal[tuple(sorted(_VALID_ROLES))]  # type: ignore[valid-type]


class _LLMTextPayload(BaseModel):
    paragraphs: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class _LLMTablePayload(BaseModel):
    rows: list[list[str]] = Field(default_factory=list)
    caption: str | None = None

    model_config = {"extra": "forbid"}


class _LLMEquationPayload(BaseModel):
    latex: str | None = None
    fallback_text: str | None = None

    model_config = {"extra": "forbid"}


class _LLMFigurePayload(BaseModel):
    # A real figure caption is a short label (e.g. "图 1-1-1"). The bound is
    # enforced by xgrammar during decoding so the model cannot spill body text
    # into a caption.
    caption: str | None = Field(default=None, max_length=120)

    model_config = {"extra": "forbid"}


class _LLMBlockContent(BaseModel):
    """Only typed payload fields the model may produce.

    The model is intentionally not allowed to emit legacy HTML table fields,
    asset fields, text segments, or generic sibling caption/math fields.
    """

    text: _LLMTextPayload | None = None
    table: _LLMTablePayload | None = None
    equation: _LLMEquationPayload | None = None
    figure: _LLMFigurePayload | None = None

    model_config = {"extra": "forbid"}


class _LLMBlock(BaseModel):
    # ``content_type`` is deliberately absent: it was always required to equal
    # ``kind`` (see PageBlueprint.align_kind_zone), so asking the model to emit
    # both is redundant and a source of contradictory output. The model emits
    # only ``kind``; PageBlueprint derives ``content_type`` from it.
    id: str
    kind: BlockKind
    zone: Zone = "main"
    role: _RoleEnum = "body"  # type: ignore[assignment]
    bbox: Annotated[list[float], Field(min_length=4, max_length=4)]
    anchor_id: str | None = None
    figure_group_id: str | None = None
    content: _LLMBlockContent = Field(default_factory=_LLMBlockContent)

    model_config = {"extra": "forbid"}


class _LLMPageBlueprint(BaseModel):
    page_no: int = Field(ge=1)
    page_size: list[int] = Field(default_factory=lambda: [1000, 1000])
    blocks: list[_LLMBlock] = Field(min_length=1)

    model_config = {"extra": "forbid"}


# ── strictify ─────────────────────────────────────────────────────────────

def _inline_refs(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Recursively inline $ref definitions to avoid deep $defs nesting."""
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        resolved = copy.deepcopy(defs.get(ref_name, {}))
        extras = {k: v for k, v in schema.items() if k != "$ref"}
        resolved.update(extras)
        return _inline_refs(resolved, defs)
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if isinstance(value, dict):
            result[key] = _inline_refs(value, defs)
        elif isinstance(value, list):
            result[key] = [
                _inline_refs(item, defs) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Transform a Pydantic JSON schema into vLLM strict guided-decoding form.

    Steps applied:
    1. Inline all $defs/$ref so xgrammar sees a flat tree.
    2. Strip keywords that xgrammar handles poorly.
    3. For every object node: add additionalProperties=false and ensure
       every property key appears in required (strict mode requirement).
    4. Remove the now-redundant $defs key.
    """
    defs = schema.get("$defs", {})

    def _process(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        # Inline refs first
        node = _inline_refs(node, defs)
        # Recurse into child nodes
        processed: dict[str, Any] = {}
        for k, v in node.items():
            if k in _STRIP_KEYWORDS:
                continue
            if k == "$defs":
                continue
            if isinstance(v, dict):
                processed[k] = _process(v)
            elif isinstance(v, list):
                processed[k] = [_process(i) if isinstance(i, dict) else i for i in v]
            else:
                processed[k] = v
        # Enforce strict object rules
        if processed.get("type") == "object" and "properties" in processed:
            processed["additionalProperties"] = False
            existing_required = set(processed.get("required", []))
            all_props = list(processed["properties"].keys())
            processed["required"] = [p for p in all_props if p in existing_required] + [
                p for p in all_props if p not in existing_required
            ]
        return processed

    return _process(copy.deepcopy(schema))


@lru_cache(maxsize=1)
def llm_page_blueprint_schema() -> dict[str, Any]:
    """Return the strictified JSON Schema for vLLM guided decoding (cached)."""
    raw = _LLMPageBlueprint.model_json_schema()
    return strictify(raw)


__all__ = [
    "_LLMTextPayload",
    "_LLMTablePayload",
    "_LLMEquationPayload",
    "_LLMFigurePayload",
    "_LLMBlockContent",
    "_LLMBlock",
    "_LLMPageBlueprint",
    "strictify",
    "llm_page_blueprint_schema",
]
