"""Convert DocumentBlock / rebuild JSON into PageUnit lists."""
from __future__ import annotations

from typing import Any

from app.services.document_ir import DocumentBlock
from app.services.ir.page_fusion.schemas import PageUnit, RebuildUnitsResult


def blocks_to_units(blocks: list[DocumentBlock], page_no: int) -> list[PageUnit]:
    page = sorted(
        [b for b in blocks if b.page_no == page_no],
        key=lambda b: (b.order, b.id),
    )
    out: list[PageUnit] = []
    for b in page:
        meta = dict(b.meta or {})
        out.append(
            PageUnit(
                id=f"m_{b.id}",
                type=b.type or "paragraph",
                zone=str(meta.get("layout_zone") or "main"),
                pedagogy_role=str(meta.get("pedagogy_role") or "body"),
                bbox=list(b.bbox) if b.bbox else None,
                text=(b.text or "").strip(),
                latex=(b.text or "").strip() if b.type == "equation" else None,
                source="mineru",
                block_id=b.id,
                asset_url=b.asset_url,
                asset_key=b.asset_key,
                caption=b.caption,
                level=b.level,
                order=b.order,
                meta=meta,
            )
        )
    return out


def parse_rebuild_units(raw: dict[str, Any], page_no: int) -> list[PageUnit]:
    data = dict(raw or {})
    if data.get("page_no") is None:
        data["page_no"] = page_no
    result = RebuildUnitsResult.model_validate(data)
    units: list[PageUnit] = []
    for i, u in enumerate(result.units):
        uid = (u.id or "").strip() or f"u{i+1}"
        units.append(
            u.model_copy(
                update={
                    "id": uid,
                    "source": "35b",
                    "block_id": None,
                    "order": i + 1,
                }
            )
        )
    return units
