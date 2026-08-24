"""Infer textbook columns from block bboxes (0–1000 normalized space).

Pages are usually one main column. A true left/right aside is a
*protruding island*:
  - horizontally clear of the main band (gutter), and
  - at the same page height, sitting beside main content (Y-overlap companion),
    not a short main-flow line that merely fails to span full width.

Strategy:
  1. Estimate the main column from **wide** blocks (body paragraphs).
  2. Collect narrow candidates fully outside the main edge.
  3. Keep only candidates with a wide/main companion at overlapping Y
     (side-by-side). Short headings alone on their line stay ``main``.
  4. Wide blocks always stay in ``main``; prefer ``main`` on ambiguity.

Magic thresholds come from Edition Bundle geometry (named params), not
hardcoded textbook names.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Sequence

from app.services.editions.geometry import ColumnGeometry, geometry_from_edition

PAGE_W = 1000.0
# Every threshold lives on ColumnGeometry (Edition Bundle data). Its field
# defaults are the _base seed values — do not reintroduce module constants,
# or per-textbook tuning silently stops taking effect.


@dataclass(frozen=True)
class ColumnBand:
    x0: float
    x1: float
    zone: str

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    def overlap(self, bx0: float, bx1: float) -> float:
        return max(0.0, min(self.x1, bx1) - max(self.x0, bx0))


@dataclass
class ColumnLayout:
    bands: list[ColumnBand] = field(default_factory=list)
    gutters: list[tuple[float, float]] = field(default_factory=list)
    column_count: int = 1
    method: str = "single"
    main_x0: float = 0.0
    main_x1: float = PAGE_W
    # Body-text edges before aside carving. ``main_x0/main_x1`` get stretched to
    # swallow any narrow block that failed the aside gate, which hides exactly
    # the margin notes we want to re-examine — feature extraction uses these.
    raw_main_x0: float = 0.0
    raw_main_x1: float = PAGE_W
    # Validated aside islands: (y0, y1) spans that passed companion check
    left_spans: list[tuple[float, float]] = field(default_factory=list)
    right_spans: list[tuple[float, float]] = field(default_factory=list)
    geom: ColumnGeometry = field(default_factory=ColumnGeometry)
    _ref_boxes: list[tuple[float, float, float, float]] = field(
        default_factory=list, repr=False
    )

    def zone_for_bbox(self, bbox: Sequence[float] | None) -> str:
        if not bbox or len(bbox) < 4 or not self.bands:
            return "main"
        g = self.geom
        x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        width = x1 - x0

        if width >= g.page_w * g.full_width_ratio or width >= g.wide_min:
            return "main"

        left_band = next((b for b in self.bands if b.zone == "aside_left"), None)
        right_band = next((b for b in self.bands if b.zone == "aside_right"), None)
        box = (x0, y0, x1, y1)

        if left_band and width <= g.aside_max_width:
            in_gutter = x1 <= self.main_x0 - g.min_gutter * 0.5 and x0 < self.main_x0
            left_ov = left_band.overlap(x0, x1)
            main_ov = max(0.0, min(self.main_x1, x1) - max(self.main_x0, x0))
            in_band = left_ov > 0 and left_ov >= main_ov and x1 <= left_band.x1 + 8
            if (in_gutter or in_band) and _span_hits(self.left_spans, y0, y1, g):
                if self._ref_boxes and not _has_side_companion(
                    box, self._ref_boxes, side="left", g=g
                ):
                    return "main"
                return "aside_left"

        if right_band and width <= g.aside_max_width:
            in_gutter = x0 >= self.main_x1 + g.min_gutter * 0.5
            right_ov = right_band.overlap(x0, x1)
            main_ov = max(0.0, min(self.main_x1, x1) - max(self.main_x0, x0))
            in_band = right_ov > 0 and right_ov >= main_ov and x0 >= right_band.x0 - 8
            if (in_gutter or in_band) and _span_hits(self.right_spans, y0, y1, g):
                if self._ref_boxes and not _has_side_companion(
                    box, self._ref_boxes, side="right", g=g
                ):
                    return "main"
                return "aside_right"

        return "main"


def _box(bb: Sequence[float]) -> tuple[float, float, float, float] | None:
    if not bb or len(bb) < 4:
        return None
    x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if (x1 - x0) < 8:
        return None
    return x0, y0, x1, y1


def _y_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _span_hits(
    spans: list[tuple[float, float]],
    y0: float,
    y1: float,
    g: ColumnGeometry,
) -> bool:
    if not spans:
        return False
    for s0, s1 in spans:
        if max(0.0, min(y1, s1) - max(y0, s0)) >= g.min_y_overlap * 0.5:
            return True
    return False


def _merge_spans(
    boxes: list[tuple[float, float, float, float]],
    *,
    pad: float = 12.0,
) -> list[tuple[float, float]]:
    if not boxes:
        return []
    intervals = sorted((b[1] - pad, b[3] + pad) for b in boxes)
    merged: list[list[float]] = [[intervals[0][0], intervals[0][1]]]
    for y0, y1 in intervals[1:]:
        if y0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], y1)
        else:
            merged.append([y0, y1])
    return [(a, b) for a, b in merged]


def _has_side_companion(
    cand: tuple[float, float, float, float],
    others: list[tuple[float, float, float, float]],
    *,
    side: str,
    g: ColumnGeometry,
) -> bool:
    """True when a wide/main block shares this height (side-by-side island)."""
    cx0, _cy0, cx1, _cy1 = cand
    for o in others:
        if o == cand:
            continue
        if _y_overlap(cand, o) < g.min_y_overlap:
            continue
        ox0, _oy0, ox1, _oy1 = o
        ow = ox1 - ox0
        if ow < g.min_companion_width:
            continue
        if side == "left":
            # Companion sits to the right; no 2D box intersection required,
            # but x-intervals must not heavily overlap (clear gutter).
            if ox0 >= cx1 - 8:
                return True
        else:
            if ox1 <= cx0 + 8:
                return True
    return False


def _main_edges(
    boxes: list[tuple[float, float, float, float]],
    g: ColumnGeometry,
) -> tuple[float, float] | None:
    """Estimate main column [x0, x1] from wide body-like boxes."""
    wide = [b for b in boxes if (b[2] - b[0]) >= g.wide_min]
    if len(wide) >= 2:
        x0s = sorted(b[0] for b in wide)
        x1s = sorted(b[2] for b in wide)
        qi = min(len(x0s) - 1, max(0, (3 * len(x0s)) // 4))
        main_x0 = float(x0s[qi])
        hi = float(x0s[-1])
        hi_support = sum(1 for x in x0s if x >= hi - 40)
        if hi_support >= 2 and hi - float(median(x0s)) >= 80:
            main_x0 = max(main_x0, hi - 20.0)
        main_x1 = float(x1s[min(len(x1s) - 1, (4 * len(x1s)) // 5)])
        main_x1 = max(main_x1, float(median(x1s)))
        return main_x0, main_x1
    if len(wide) == 1:
        return wide[0][0], wide[0][2]
    if not boxes:
        return None
    widest = max(boxes, key=lambda b: b[2] - b[0])
    if (widest[2] - widest[0]) >= 280:
        return widest[0], widest[2]
    return None


def _left_aside_boxes(
    boxes: list[tuple[float, float, float, float]],
    main_x0: float,
    g: ColumnGeometry,
) -> list[tuple[float, float, float, float]]:
    out = []
    for x0, y0, x1, y1 in boxes:
        if (x1 - x0) > g.aside_max_width:
            continue
        if x1 <= main_x0 - g.min_gutter:
            out.append((x0, y0, x1, y1))
    return out


def _right_aside_boxes(
    boxes: list[tuple[float, float, float, float]],
    main_x1: float,
    g: ColumnGeometry,
) -> list[tuple[float, float, float, float]]:
    out = []
    for x0, y0, x1, y1 in boxes:
        if (x1 - x0) > g.aside_max_width:
            continue
        if x0 >= main_x1 + g.min_gutter:
            out.append((x0, y0, x1, y1))
    return out


def _filter_with_companions(
    cands: list[tuple[float, float, float, float]],
    boxes: list[tuple[float, float, float, float]],
    *,
    side: str,
    g: ColumnGeometry,
) -> list[tuple[float, float, float, float]]:
    return [c for c in cands if _has_side_companion(c, boxes, side=side, g=g)]


def _aside_is_meaningful(
    aside_boxes: list[tuple[float, float, float, float]],
    *,
    side: str,
    g: ColumnGeometry,
    profile_edge: float | None = None,
) -> bool:
    if not aside_boxes:
        return False
    tall = sum(1 for _x0, y0, _x1, y1 in aside_boxes if (y1 - y0) >= 50)
    if tall >= 1:
        return True
    if len(aside_boxes) >= 2:
        span = max(b[3] for b in aside_boxes) - min(b[1] for b in aside_boxes)
        if span >= 50:
            return True
    # Margin notes past the profile edge (short 「∞」旁注 / tip strip)
    if profile_edge is not None:
        if side == "right":
            if any(
                b[0] >= profile_edge and (b[2] - b[0]) <= g.aside_max_width
                for b in aside_boxes
            ):
                return True
        if side == "left":
            if any(
                b[2] <= profile_edge
                and (b[2] - b[0]) <= g.aside_max_width
                and (b[3] - b[1]) >= 40
                for b in aside_boxes
            ):
                return True
    if len(aside_boxes) == 1:
        x0, y0, x1, y1 = aside_boxes[0]
        w, h = x1 - x0, y1 - y0
        if h >= 90 and w <= 220:
            return True
    return False


def detect_columns(
    bboxes: Sequence[Sequence[float] | None],
    *,
    page_w: float = PAGE_W,
    left_x1_max: float = 280.0,
    right_x0_min: float = 680.0,
    geometry: ColumnGeometry | None = None,
    edition_id: str | None = None,
) -> ColumnLayout:
    """Infer column bands from a page's block bboxes.

    Prefer ``edition_id`` / ``geometry`` for thresholds; legacy
    ``left_x1_max`` / ``right_x0_min`` still accepted.
    """
    g = geometry or (geometry_from_edition(edition_id) if edition_id else None)
    if g is None:
        # Legacy callers pass only the two zone edges; keep their values.
        g = ColumnGeometry(
            page_w=page_w,
            aside_left_x1_max=left_x1_max,
            aside_right_x0_min=right_x0_min,
        )
    page_w = g.page_w
    left_x1_max = g.aside_left_x1_max
    right_x0_min = g.aside_right_x0_min

    boxes: list[tuple[float, float, float, float]] = []
    for bb in bboxes:
        b = _box(bb)
        if b is None:
            continue
        x0, y0, x1, y1 = b
        if (x1 - x0) >= page_w * g.full_width_ratio:
            continue
        boxes.append(b)

    empty = ColumnLayout(
        bands=[ColumnBand(0.0, page_w, "main")],
        column_count=1,
        method="single",
        main_x0=0.0,
        main_x1=page_w,
        raw_main_x0=0.0,
        raw_main_x1=page_w,
        geom=g,
        _ref_boxes=[],
    )
    if not boxes:
        return empty

    edges = _main_edges(boxes, g)
    if edges is None:
        return empty

    main_x0, main_x1 = edges
    raw_main_x0, raw_main_x1 = main_x0, main_x1
    left_cands = _filter_with_companions(
        _left_aside_boxes(boxes, main_x0, g), boxes, side="left", g=g
    )
    right_cands = _filter_with_companions(
        _right_aside_boxes(boxes, main_x1, g), boxes, side="right", g=g
    )

    if not right_cands:
        profile_right = [
            b
            for b in boxes
            if b[0] >= right_x0_min and (b[2] - b[0]) <= g.aside_max_width
        ]
        right_cands = _filter_with_companions(
            profile_right, boxes, side="right", g=g
        )

    has_left = _aside_is_meaningful(
        left_cands, side="left", g=g, profile_edge=left_x1_max
    )
    has_right = _aside_is_meaningful(
        right_cands, side="right", g=g, profile_edge=right_x0_min
    )

    bands: list[ColumnBand] = []
    gutters: list[tuple[float, float]] = []
    left_spans: list[tuple[float, float]] = []
    right_spans: list[tuple[float, float]] = []

    if has_left:
        left_x1 = min(max(b[2] for b in left_cands), main_x0 - g.min_gutter)
        left_x1 = max(40.0, left_x1)
        bands.append(ColumnBand(0.0, left_x1, "aside_left"))
        gutters.append((left_x1, main_x0))
        main_lo = main_x0
        left_spans = _merge_spans(left_cands)
    else:
        main_lo = min(main_x0, min(b[0] for b in boxes))

    if has_right:
        right_x0 = min(b[0] for b in right_cands)
        right_x0 = max(min(right_x0, right_x0_min), main_x1 + g.min_gutter * 0.25)
        if right_x0 < right_x0_min and min(b[0] for b in right_cands) >= right_x0_min:
            right_x0 = right_x0_min
        main_hi = min(main_x1, right_x0 - g.min_gutter)
        bands.append(ColumnBand(main_lo, max(main_lo + 40.0, main_hi), "main"))
        bands.append(ColumnBand(right_x0, page_w, "aside_right"))
        gutters.append((main_hi, right_x0))
        right_spans = _merge_spans(right_cands)
    else:
        main_hi = max(main_x1, max(b[2] for b in boxes))
        bands.append(ColumnBand(main_lo, min(page_w, main_hi + 20), "main"))

    if not any(b.zone == "main" for b in bands):
        bands = [ColumnBand(0.0, page_w, "main")]
        has_left = has_right = False
        main_lo, main_hi = 0.0, page_w
        left_spans, right_spans = [], []

    count = 1 + int(has_left) + int(has_right)
    return ColumnLayout(
        bands=bands,
        gutters=gutters,
        column_count=count,
        method="main_edge",
        main_x0=main_lo,
        main_x1=main_hi,
        raw_main_x0=raw_main_x0,
        raw_main_x1=raw_main_x1,
        left_spans=left_spans,
        right_spans=right_spans,
        geom=g,
        _ref_boxes=list(boxes),
    )


def assign_layout_zone(
    bbox: Sequence[float] | None,
    layout: ColumnLayout,
    *,
    forced: str | None = None,
) -> str:
    if forced:
        return forced
    return layout.zone_for_bbox(bbox)


def content_y_band(
    bboxes: Sequence[Sequence[float] | None],
) -> tuple[float, float]:
    """Min/max content Y in 0–1000 space (for aside vertical placement)."""
    ys: list[float] = []
    for bb in bboxes:
        b = _box(bb)
        if b is None:
            continue
        ys.append(b[1])
        ys.append(b[3])
    if not ys:
        return 0.0, PAGE_W
    return min(ys), max(ys)


def aside_top_pct(
    bbox: Sequence[float] | None,
    band_top: float,
    band_bot: float,
) -> float:
    """Map block y0 into 0–100% of the content band (legacy helper)."""
    if not bbox or len(bbox) < 2:
        return 0.0
    y0 = float(bbox[1])
    span = max(1.0, band_bot - band_top)
    return max(0.0, min(95.0, (y0 - band_top) / span * 100.0))


# Full-page content height ≈ this many em (for margin-top mapping).
_ASIDE_PAGE_EM = 48.0


def aside_gap_em(
    y0: float,
    prev_bottom: float,
    *,
    page_em: float = _ASIDE_PAGE_EM,
) -> float:
    """Normal-flow margin-top (em) from previous aside bottom → this y0."""
    gap = max(0.0, float(y0) - float(prev_bottom))
    return min(page_em, gap / PAGE_W * page_em)
