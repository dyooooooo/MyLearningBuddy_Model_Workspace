"""XY-cut column bands — the standard whitespace-projection segmentation.

Replaces ad-hoc gutter guessing with the classical algorithm: project block
boxes onto the X axis, find whitespace valleys wide enough to be real gutters,
and split. A sparse-page guard keeps title/cover pages (few boxes that merely
happen to sit at two X positions) from being reported as multi-column.

Output is *evidence*, not a verdict — :mod:`decide` feeds it to rules
alongside the geometric baseline.
"""
from __future__ import annotations

from dataclasses import dataclass

Box = tuple[float, float, float, float]

# A column needs at least this many boxes, else it is a sparse-layout artefact.
_MIN_BOXES_PER_COLUMN = 3


@dataclass(frozen=True)
class XYCutResult:
    columns: tuple[tuple[float, float], ...] = ()
    gutters: tuple[tuple[float, float], ...] = ()

    @property
    def column_count(self) -> int:
        return len(self.columns) or 1

    def column_of(self, x0: float, x1: float) -> int:
        """Index of the column holding most of ``[x0, x1]``; -1 when unknown."""
        if not self.columns:
            return -1
        best_idx, best_ov = -1, 0.0
        for idx, (cx0, cx1) in enumerate(self.columns):
            ov = max(0.0, min(cx1, x1) - max(cx0, x0))
            if ov > best_ov:
                best_idx, best_ov = idx, ov
        return best_idx


def _x_gutters(
    boxes: list[Box],
    *,
    page_w: float,
    min_gutter: float,
) -> list[tuple[float, float]]:
    """Whitespace valleys on the X projection, wide enough to be gutters."""
    if not boxes:
        return []
    spans = sorted((b[0], b[2]) for b in boxes)
    gutters: list[tuple[float, float]] = []
    cursor = spans[0][1]
    for x0, x1 in spans[1:]:
        if x0 - cursor >= min_gutter:
            gutters.append((cursor, x0))
        cursor = max(cursor, x1)
    # Ignore margins: a gutter touching either page edge is not a column split.
    return [g for g in gutters if g[0] > 1.0 and g[1] < page_w - 1.0]


def xy_cut(
    boxes: list[Box],
    *,
    page_w: float = 1000.0,
    min_gutter: float = 18.0,
    min_boxes_per_column: int = _MIN_BOXES_PER_COLUMN,
) -> XYCutResult:
    """Split a page into column bands along vertical whitespace."""
    usable = [b for b in boxes if (b[2] - b[0]) > 0]
    if len(usable) < min_boxes_per_column * 2:
        return XYCutResult()

    gutters = _x_gutters(usable, page_w=page_w, min_gutter=min_gutter)
    if not gutters:
        return XYCutResult()

    edges = [0.0]
    for g0, g1 in gutters:
        edges.append((g0 + g1) / 2.0)
    edges.append(page_w)

    columns: list[tuple[float, float]] = []
    kept_gutters: list[tuple[float, float]] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        members = [b for b in usable if b[0] >= lo - 1.0 and b[2] <= hi + 1.0]
        if not members:
            continue
        columns.append((min(b[0] for b in members), max(b[2] for b in members)))

    # Sparse-layout guard: a "column" backed by one or two boxes is an artefact
    # of a title page, not a real column.
    if len(columns) > 1:
        dense = []
        for cx0, cx1 in columns:
            members = [b for b in usable if b[0] >= cx0 - 1.0 and b[2] <= cx1 + 1.0]
            if len(members) >= min_boxes_per_column:
                dense.append((cx0, cx1))
        if len(dense) < 2:
            return XYCutResult()
        columns = dense
        kept_gutters = [
            (columns[i][1], columns[i + 1][0]) for i in range(len(columns) - 1)
        ]

    if len(columns) < 2:
        return XYCutResult()
    return XYCutResult(columns=tuple(columns), gutters=tuple(kept_gutters))
