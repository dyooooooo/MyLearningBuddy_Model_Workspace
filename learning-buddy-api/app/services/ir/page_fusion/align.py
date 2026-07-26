"""Align MinerU and 35B page units by bbox IoU + type."""
from __future__ import annotations

from app.services.ir.page_fusion.schemas import AlignedUnit, PageUnit

_IOU_THRESH = 0.25


def _bbox_iou(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return 0.0
    ax0, ay0, ax1, ay1 = (float(a[0]), float(a[1]), float(a[2]), float(a[3]))
    bx0, by0, bx1, by1 = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
    if ax1 < ax0:
        ax0, ax1 = ax1, ax0
    if ay1 < ay0:
        ay0, ay1 = ay1, ay0
    if bx1 < bx0:
        bx0, bx1 = bx1, bx0
    if by1 < by0:
        by0, by1 = by1, by0
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _type_compatible(a: str, b: str) -> bool:
    a = (a or "").lower()
    b = (b or "").lower()
    if a == b:
        return True
    group = {
        "paragraph", "heading", "title", "list", "caption",
        "equation",
        "figure",
        "table",
    }
    # text-like group
    textish = {"paragraph", "heading", "title", "list", "caption"}
    if a in textish and b in textish:
        return True
    return False


def align_units(
    mineru: list[PageUnit],
    b35: list[PageUnit],
) -> list[AlignedUnit]:
    """Greedy best-IoU matching; leftovers become only-sides."""
    used_b: set[int] = set()
    aligned: list[AlignedUnit] = []
    aid = 0

    for mu in mineru:
        best_j = -1
        best_iou = 0.0
        for j, bu in enumerate(b35):
            if j in used_b:
                continue
            if not _type_compatible(mu.type, bu.type):
                # still allow if same zone=running (chrome)
                if not (
                    mu.zone == "running"
                    and bu.zone == "running"
                ):
                    continue
            iou = _bbox_iou(mu.bbox, bu.bbox)
            # text fallback when bbox weak
            if iou < 0.05 and mu.text and bu.text:
                if mu.text.strip() == bu.text.strip():
                    iou = max(iou, 0.3)
                elif mu.text.strip() in bu.text.strip() or bu.text.strip() in mu.text.strip():
                    iou = max(iou, 0.28)
            if iou > best_iou:
                best_iou = iou
                best_j = j
        aid += 1
        uid = f"a{aid}"
        if best_j >= 0 and best_iou >= _IOU_THRESH:
            used_b.add(best_j)
            aligned.append(
                AlignedUnit(
                    unit_id=uid,
                    kind="paired",
                    mineru=mu,
                    b35=b35[best_j],
                )
            )
        else:
            aligned.append(
                AlignedUnit(unit_id=uid, kind="mineru_only", mineru=mu, b35=None)
            )

    for j, bu in enumerate(b35):
        if j in used_b:
            continue
        aid += 1
        aligned.append(
            AlignedUnit(unit_id=f"a{aid}", kind="35b_only", mineru=None, b35=bu)
        )

    return aligned
