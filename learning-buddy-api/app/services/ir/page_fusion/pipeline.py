"""Orchestrate layout_rebuild: rebuild → align → judge → fuse."""
from __future__ import annotations

from typing import Any

from app.services.document_ir import DocumentBlock
from app.services.ir.page_fusion.align import align_units
from app.services.ir.page_fusion.fuse import fuse_page_blocks, judge_summary
from app.services.ir.page_fusion.units import blocks_to_units
from app.services.ir.page_fusion.vision import judge_aligned_units, rebuild_units_from_image


def run_layout_rebuild(
    *,
    page_no: int,
    page_blocks: list[DocumentBlock],
    jpeg: bytes,
    user_note: str = "",
    edition_id: str | None = None,
) -> tuple[list[DocumentBlock], dict[str, Any]]:
    """Return fused page blocks + debug dump for correction/meta."""
    mineru_units = blocks_to_units(page_blocks, page_no)
    b35_units = rebuild_units_from_image(
        jpeg, page_no, user_note=user_note, edition_id=edition_id
    )
    aligned = align_units(mineru_units, b35_units)
    judge = judge_aligned_units(
        jpeg, page_no, aligned, user_note=user_note, edition_id=edition_id
    )
    fused = fuse_page_blocks(
        page_no=page_no, aligned=aligned, decisions=judge.decisions
    )
    summary = judge_summary(aligned, judge.decisions)
    dump: dict[str, Any] = {
        "mode": "layout_rebuild",
        "page_no": page_no,
        "mineru_units": len(mineru_units),
        "b35_units": len(b35_units),
        "aligned": len(aligned),
        "fused_blocks": len(fused),
        "judge_summary": summary,
        "decisions": [d.model_dump() for d in judge.decisions],
        "aligned_preview": [
            {
                "unit_id": a.unit_id,
                "kind": a.kind,
                "mineru_text": (a.mineru.text if a.mineru else "")[:60],
                "b35_text": (a.b35.text if a.b35 else "")[:60],
            }
            for a in aligned[:24]
        ],
    }
    return fused, dump
