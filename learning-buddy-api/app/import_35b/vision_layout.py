"""35B page-blueprint generation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app.clients.vllm import vision_json_completion
from app.import_35b.model_boundary import repair_blueprint_dict
from app.import_35b.schemas import PageBlueprint
from app.import_35b.schemas.llm_view import llm_page_blueprint_schema

PROMPT_VERSION = "layout_blueprint_v6"
_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.md"


def load_layout_prompt(edition_id: str | None = None) -> str:
    """Base 35B layout prompt; when edition_id set, inject Edition policy."""
    eid = (edition_id or "").strip()
    if eid:
        try:
            from app.services.editions.assembler import assemble_prompt

            return assemble_prompt("b35.vision_layout", eid)
        except (FileNotFoundError, ValueError, KeyError):
            pass
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _user_payload(
    page_no: int,
    layout_hints: list[dict] | None = None,
    profile: dict | None = None,
) -> str:
    parts = [
        "请分析本页教材原图，输出 PageBlueprint JSON。\n"
        + json.dumps(
            {
                "page_no": page_no,
                "page_size": [1000, 1000],
            },
            ensure_ascii=False,
        )
    ]
    if layout_hints:
        # Mirror vision_page_refine_json: attach MinerU layout as structured JSON.
        parts.append(
            "\nMinerU layout JSON:\n" + json.dumps(layout_hints, ensure_ascii=False)
        )
    if profile:
        zones = profile.get("zones") or {}
        if zones:
            parts.append(
                "\nProfile hints:\n"
                + json.dumps(
                    {
                        "profile_id": profile.get("profile_id"),
                        "zones": zones,
                        "features": {
                            k: v
                            for k, v in (profile.get("features") or {}).items()
                            if k
                            in (
                                "columns",
                                "running_header_footer",
                                "aside",
                                "toc_style",
                                "figure_policy",
                                "math_density",
                            )
                        },
                    },
                    ensure_ascii=False,
                )
            )
    return "".join(parts)


def generate_page_blueprint(
    *,
    page_no: int,
    page_jpeg: bytes,
    system: str | None = None,
    max_tokens: int = 6144,
    debug: dict[str, Any] | None = None,
    layout_hints: list[dict] | None = None,
    profile: dict | None = None,
) -> PageBlueprint:
    """Call 35B Vision and validate the resulting PageBlueprint.

    When ``debug`` is provided it is populated with the *raw* model output
    (``raw_model``, before repair/normalize) plus the low-level call metrics from
    ``vision_json_completion`` — this is the missing pre-repair snapshot needed to
    tell whether a bad page came from the model or from post-processing.

    When ``layout_hints`` is provided (MinerU structure), the user payload includes
    those boxes/types so the model fills content under structural constraints.
    """
    from app.config import settings

    response_schema = llm_page_blueprint_schema() if settings.vllm_use_json_schema else None
    edition_id = None
    if profile:
        edition_id = str(profile.get("edition_id") or profile.get("profile_id") or "").strip() or None
    raw = vision_json_completion(
        page_jpeg,
        _user_payload(page_no, layout_hints=layout_hints, profile=profile),
        system=system or load_layout_prompt(edition_id),
        max_tokens=max_tokens,
        response_schema=response_schema,
        schema_name="page_blueprint",
        debug=debug,
    )
    if debug is not None:
        # Snapshot the untouched model output before we mutate/validate it.
        debug["raw_model"] = copy.deepcopy(raw)
    # v4 净化面：防腐层——JSON 转义损伤白名单还原 + LaTeX 环境自动闭合。
    raw, boundary_repairs = repair_blueprint_dict(raw)
    if debug is not None:
        debug["boundary_repairs"] = boundary_repairs
        debug["repaired_model"] = copy.deepcopy(raw)
    raw.setdefault("page_no", page_no)
    raw.setdefault("page_size", [1000, 1000])
    # Drop legacy field if the model echoes it from older prompts.
    raw.pop("column_count_hint", None)
    blueprint = PageBlueprint.model_validate(raw)
    if blueprint.page_no != page_no:
        raise ValueError(
            f"blueprint page_no mismatch: expected {page_no}, got {blueprint.page_no}"
        )
    return blueprint


def blueprint_to_dict(blueprint: PageBlueprint) -> dict[str, Any]:
    return blueprint.model_dump(mode="json")


_REFINE_BLOCK_SYSTEM = """你是教材版式块精修助手。根据整页原图与给定块 JSON，只输出**一个** PageBlueprint block 对象（JSON）。
要求：
- 保持原 block 的 id；可修正 kind/zone/role/bbox/content
- 不要输出整页 blueprint，不要包在 blocks 数组里
- 文本/LaTeX 按图校正；figure caption 仅短标签
- 只返回 JSON 对象，无其它说明"""


def refine_single_block(
    *,
    page_jpeg: bytes,
    page_no: int,
    block: dict,
    note: str = "",
    layout_hint: dict | None = None,
) -> dict:
    """Vision-refine one PageBlueprint block; returns a single block dict."""
    payload: dict[str, Any] = {
        "page_no": page_no,
        "block": block,
        "note": (note or "").strip(),
    }
    if layout_hint:
        payload["layout_hint"] = layout_hint
    user_text = (
        "请精修下列块，返回单个 block JSON（与 PageBlueprint.blocks[] 元素同形）。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    raw = vision_json_completion(
        page_jpeg,
        user_text,
        system=_REFINE_BLOCK_SYSTEM,
        max_tokens=2048,
    )
    if isinstance(raw, dict) and "blocks" in raw and isinstance(raw["blocks"], list):
        blocks = [b for b in raw["blocks"] if isinstance(b, dict)]
        if blocks:
            raw = blocks[0]
    if not isinstance(raw, dict):
        raise ValueError("refine_single_block: model did not return a block object")
    # Preserve id if model dropped it
    if block.get("id") and not raw.get("id"):
        raw["id"] = block["id"]
    return raw
