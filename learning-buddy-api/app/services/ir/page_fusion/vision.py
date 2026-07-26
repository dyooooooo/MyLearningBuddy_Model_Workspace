"""35B layout rebuild + unit judge vision calls."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.clients.vllm import vision_json_completion
from app.services.ir.page_fusion.schemas import (
    AlignedUnit,
    JudgeDecision,
    JudgeResult,
    PageUnit,
)
from app.services.ir.page_fusion.units import parse_rebuild_units

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[4] / "data" / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _edition_system(slot_id: str, edition_id: str | None, fallback_name: str) -> str:
    """Assemble edition policy into system prompt; fall back to raw MD."""
    eid = (edition_id or "").strip()
    if eid:
        try:
            from app.services.editions.assembler import assemble_prompt

            return assemble_prompt(slot_id, eid)
        except (FileNotFoundError, ValueError, KeyError):
            pass
    return _load_prompt(fallback_name)


def rebuild_units_from_image(
    jpeg: bytes,
    page_no: int,
    *,
    user_note: str = "",
    edition_id: str | None = None,
) -> list[PageUnit]:
    system = _edition_system(
        "mineru.layout_rebuild",
        edition_id,
        "mineru_page_layout_rebuild_v1.md",
    )
    note = (user_note or "").strip()
    note_block = f"\n\n## 用户改进建议\n{note}\n" if note else ""
    user_text = f"page_no={page_no}\n请根据页图重建本页全部最小单元。{note_block}"
    raw = vision_json_completion(
        jpeg,
        user_text,
        system=system,
        max_tokens=8192,
    )
    return parse_rebuild_units(raw, page_no)


def _aligned_payload(aligned: list[AlignedUnit]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for au in aligned:
        row: dict[str, Any] = {
            "unit_id": au.unit_id,
            "kind": au.kind,
        }
        if au.mineru is not None:
            row["mineru"] = {
                "type": au.mineru.type,
                "zone": au.mineru.zone,
                "pedagogy_role": au.mineru.pedagogy_role,
                "bbox": au.mineru.bbox,
                "text": (au.mineru.text or "")[:500],
            }
        if au.b35 is not None:
            row["b35"] = {
                "type": au.b35.type,
                "zone": au.b35.zone,
                "pedagogy_role": au.b35.pedagogy_role,
                "bbox": au.b35.bbox,
                "text": (au.b35.text or "")[:500],
            }
        rows.append(row)
    return rows


def judge_aligned_units(
    jpeg: bytes,
    page_no: int,
    aligned: list[AlignedUnit],
    *,
    user_note: str = "",
    edition_id: str | None = None,
) -> JudgeResult:
    system = _edition_system(
        "mineru.unit_judge",
        edition_id,
        "mineru_page_unit_judge_v1.md",
    )
    note = (user_note or "").strip()
    note_block = f"\n\n## 用户改进建议\n{note}\n" if note else ""
    payload = _aligned_payload(aligned)
    user_text = (
        f"page_no={page_no}\n"
        "Aligned units JSON (decide keep_mineru | keep_35b | drop for each):\n"
        + json.dumps(payload, ensure_ascii=False)
        + note_block
    )
    raw = vision_json_completion(
        jpeg,
        user_text,
        system=system,
        max_tokens=4096,
    )
    if raw.get("page_no") is None:
        raw["page_no"] = page_no
    result = JudgeResult.model_validate(raw)
    have = {d.unit_id for d in result.decisions}
    for au in aligned:
        if au.unit_id in have:
            continue
        # Conservative defaults (same spirit as fuse.sanitize_decision).
        if au.kind == "35b_only":
            default = "drop"
        else:
            default = "keep_mineru"
        result.decisions.append(JudgeDecision(unit_id=au.unit_id, action=default))
    return result
