"""Fuse judged units back into DocumentBlock IR.

Judge decisions are trusted when MinerU baseline rollback is available;
sanitize only fixes illegal action/side combinations.
"""
from __future__ import annotations

import logging

from app.services.document_ir import DocumentBlock
from app.services.ir.page_fusion.schemas import AlignedUnit, JudgeDecision, PageUnit

logger = logging.getLogger(__name__)


def _is_chrome(unit: PageUnit | None) -> bool:
    if unit is None:
        return False
    role = (unit.pedagogy_role or "").lower()
    return unit.zone == "running" or role in (
        "running_header",
        "running_footer",
        "page_number",
    )


def sanitize_decision(au: AlignedUnit, action: str) -> str:
    """Normalize action; do not second-guess judge drops (rollback via MinerU)."""
    action = (action or "").strip().lower()
    if action not in ("keep_mineru", "keep_35b", "drop"):
        action = ""

    if not action:
        if au.kind == "paired":
            action = "keep_mineru"
        elif au.kind == "mineru_only":
            action = "keep_mineru"
        elif au.kind == "35b_only":
            action = "keep_35b" if _is_chrome(au.b35) else "drop"
        else:
            action = "drop"

    if action == "keep_mineru" and au.mineru is None:
        action = "keep_35b" if au.b35 is not None else "drop"
    if action == "keep_35b" and au.b35 is None:
        action = "keep_mineru" if au.mineru is not None else "drop"

    return action


def _merge_keep_35b(au: AlignedUnit) -> PageUnit | None:
    """Prefer 35B text/role/zone but preserve MinerU id/bbox/assets when paired."""
    b35 = au.b35
    if b35 is None:
        return au.mineru
    mu = au.mineru
    if mu is None:
        return b35
    meta = dict(mu.meta or {})
    meta.update({k: v for k, v in (b35.meta or {}).items() if v is not None})
    return PageUnit(
        id=b35.id,
        type=b35.type or mu.type,
        zone=b35.zone or mu.zone,
        pedagogy_role=b35.pedagogy_role or mu.pedagogy_role,
        bbox=b35.bbox or mu.bbox,
        text=b35.text if (b35.text or "").strip() else mu.text,
        latex=b35.latex or mu.latex,
        source="35b",
        block_id=mu.block_id,  # preserve stable MinerU id
        asset_url=mu.asset_url or b35.asset_url,
        asset_key=mu.asset_key or b35.asset_key,
        caption=b35.caption or mu.caption,
        level=b35.level if b35.level is not None else mu.level,
        order=mu.order,
        meta=meta,
    )


def _unit_to_block(unit: PageUnit, page_no: int, order: int) -> DocumentBlock:
    meta = dict(unit.meta or {})
    meta["layout_zone"] = unit.zone
    meta["pedagogy_role"] = unit.pedagogy_role
    meta["refined_by"] = "fusion"
    meta["fusion_source"] = unit.source
    if unit.zone == "running":
        meta["skip_render"] = True
    bid = unit.block_id or unit.id
    btype = unit.type if unit.type in (
        "title", "heading", "paragraph", "list", "table",
        "figure", "equation", "caption",
    ) else "paragraph"
    text = unit.text or ""
    if btype == "equation" and unit.latex:
        text = unit.latex
    return DocumentBlock(
        id=str(bid),
        type=btype,  # type: ignore[arg-type]
        page_no=page_no,
        order=order,
        text=text,
        bbox=unit.bbox,
        level=unit.level,
        caption=unit.caption,
        asset_url=unit.asset_url,
        asset_key=unit.asset_key,
        meta=meta,
    )


def fuse_page_blocks(
    *,
    page_no: int,
    aligned: list[AlignedUnit],
    decisions: list[JudgeDecision],
) -> list[DocumentBlock]:
    """Apply judge decisions; chrome de-dupe only."""
    by_id = {d.unit_id: d.action for d in decisions}
    kept: list[DocumentBlock] = []
    order = 0
    chrome_header_kept = 0
    chrome_pn_kept = 0
    seen_ids: set[str] = set()

    for au in aligned:
        action = sanitize_decision(au, by_id.get(au.unit_id) or "")

        if action == "drop":
            continue

        if action == "keep_mineru":
            unit = au.mineru
        else:
            unit = _merge_keep_35b(au) if au.kind == "paired" else au.b35

        if unit is None:
            continue

        role = (unit.pedagogy_role or "").lower()
        if unit.zone == "running" and role == "running_header":
            chrome_header_kept += 1
            if chrome_header_kept > 2:
                continue
        if unit.zone == "running" and role == "page_number":
            chrome_pn_kept += 1
            if chrome_pn_kept > 1:
                continue

        bid = str(unit.block_id or unit.id)
        if bid in seen_ids:
            continue
        seen_ids.add(bid)

        order += 1
        kept.append(_unit_to_block(unit, page_no, order))

    kept.sort(key=lambda b: (b.order, b.id))
    return kept


def judge_summary(
    aligned: list[AlignedUnit],
    decisions: list[JudgeDecision],
) -> dict[str, int]:
    counts = {
        "keep_mineru": 0,
        "keep_35b": 0,
        "drop": 0,
        "aligned": len(aligned),
        "sanitized_keep_mineru": 0,
    }
    for au in aligned:
        raw = next((d.action for d in decisions if d.unit_id == au.unit_id), "")
        final = sanitize_decision(au, raw)
        if final in counts:
            counts[final] += 1
        if raw == "drop" and final == "keep_mineru":
            counts["sanitized_keep_mineru"] += 1
    return counts
