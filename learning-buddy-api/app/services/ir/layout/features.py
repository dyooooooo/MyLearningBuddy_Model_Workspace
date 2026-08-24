"""Pure feature extraction for layout decisions — no verdicts here.

Splitting "measure the page" from "decide the zone" is what lets a bad page be
frozen into a unit-test fixture (features are plain numbers) and lets textbook
adaptation happen in rule data instead of Python edits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from app.services.editions.geometry import ColumnGeometry
from app.services.ir.layout.xycut import XYCutResult, xy_cut

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class BlockInput:
    """Engine-agnostic view of one block (keeps DocumentBlock out of this layer)."""

    key: str
    bbox: Sequence[float] | None = None
    type: str = "paragraph"
    mineru_type: str = ""
    role: str = "body"
    text: str = ""
    # Zone already fixed upstream (35B refine / page fusion); rules must opt in
    # before overriding it.
    zone_pinned: bool = False


def _norm_box(bbox: Sequence[float] | None) -> Box | None:
    if not bbox or len(bbox) < 4:
        return None
    x0, y0, x1, y1 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return x0, y0, x1, y1


def _y_overlap(a: Box, b: Box) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


@dataclass(frozen=True)
class BlockFeatures:
    key: str
    has_bbox: bool
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    width: float = 0.0
    height: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    width_ratio: float = 0.0
    is_wide: bool = False
    is_full_width: bool = False
    gutter_left: float = 0.0
    gutter_right: float = 0.0
    left_of_main: bool = False
    right_of_main: bool = False
    beyond_left_edge: bool = False
    beyond_right_edge: bool = False
    has_companion_left: bool = False
    has_companion_right: bool = False
    has_band_main: bool = False
    band_main_x0: float = 0.0
    band_main_x1: float = 0.0
    left_of_band_main: bool = False
    right_of_band_main: bool = False
    max_y_overlap: float = 0.0
    xycut_column: int = -1
    baseline_zone: str = "main"
    zone_pinned: bool = False
    type: str = "paragraph"
    mineru_type: str = ""
    role: str = "body"
    text: str = ""
    text_len: int = 0

    def as_names(self) -> dict[str, Any]:
        """Flat name -> value map for rule expressions."""
        return {
            "key": self.key,
            "has_bbox": self.has_bbox,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
            "width": self.width,
            "height": self.height,
            "cx": self.cx,
            "cy": self.cy,
            "width_ratio": self.width_ratio,
            "is_wide": self.is_wide,
            "is_full_width": self.is_full_width,
            "gutter_left": self.gutter_left,
            "gutter_right": self.gutter_right,
            "left_of_main": self.left_of_main,
            "right_of_main": self.right_of_main,
            "beyond_left_edge": self.beyond_left_edge,
            "beyond_right_edge": self.beyond_right_edge,
            "has_companion_left": self.has_companion_left,
            "has_companion_right": self.has_companion_right,
            "has_band_main": self.has_band_main,
            "band_main_x0": self.band_main_x0,
            "band_main_x1": self.band_main_x1,
            "left_of_band_main": self.left_of_band_main,
            "right_of_band_main": self.right_of_band_main,
            "max_y_overlap": self.max_y_overlap,
            "xycut_column": self.xycut_column,
            "baseline_zone": self.baseline_zone,
            "zone_pinned": self.zone_pinned,
            "type": self.type,
            "mineru_type": self.mineru_type,
            "role": self.role,
            "text": self.text,
            "text_len": self.text_len,
        }


@dataclass(frozen=True)
class PageFeatures:
    page_no: int
    page_w: float
    main_x0: float
    main_x1: float
    column_count: int
    column_method: str
    xycut: XYCutResult
    blocks: tuple[BlockFeatures, ...] = ()
    page_block_count: int = 0
    page_wide_count: int = 0
    page_text_len: int = 0
    page_figure_count: int = 0
    page_max_block_height: float = 0.0
    geometry: ColumnGeometry = field(default_factory=ColumnGeometry)

    def as_names(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "page_w": self.page_w,
            "main_x0": self.main_x0,
            "main_x1": self.main_x1,
            "column_count": self.column_count,
            "column_method": self.column_method,
            "xycut_columns": self.xycut.column_count,
            "page_block_count": self.page_block_count,
            "page_wide_count": self.page_wide_count,
            "page_text_len": self.page_text_len,
            "page_figure_count": self.page_figure_count,
            "page_max_block_height": self.page_max_block_height,
        }


def _companions(
    cand: Box,
    others: list[Box],
    geom: ColumnGeometry,
) -> tuple[bool, bool, float]:
    """(wide block to my left, wide block to my right, best Y overlap)."""
    left = right = False
    best_ov = 0.0
    for other in others:
        if other == cand:
            continue
        ov = _y_overlap(cand, other)
        if ov < geom.min_y_overlap:
            continue
        if (other[2] - other[0]) < geom.min_companion_width:
            continue
        best_ov = max(best_ov, ov)
        if other[2] <= cand[0] + 8:
            left = True
        elif other[0] >= cand[2] - 8:
            right = True
    return left, right, best_ov


def _band_main_edges(
    cand: Box,
    wide: list[Box],
    geom: ColumnGeometry,
) -> tuple[bool, float, float]:
    """Body-column edges near ``cand``'s height, from wide blocks only.

    A page-wide ``main_x0`` is wrong on mixed pages — a lesson that is
    two-column at the top and single-column at the bottom has two different
    body columns, and the page-wide minimum belongs to neither. Looking only at
    wide blocks inside a vertical window around the candidate gives the body
    column *at that height*, which is what "is this block in the margin?"
    actually means.

    Returns ``(found, x0, x1)``.
    """
    pad = max(cand[3] - cand[1], geom.band_pad_min)
    top, bottom = cand[1] - pad, cand[3] + pad

    x0 = x1 = None
    for other in wide:
        if other == cand:
            continue
        if other[3] <= top or other[1] >= bottom:
            continue
        x0 = other[0] if x0 is None else min(x0, other[0])
        x1 = other[2] if x1 is None else max(x1, other[2])
    if x0 is None or x1 is None:
        return False, 0.0, geom.page_w
    return True, x0, x1


def extract_page_features(
    blocks: Sequence[BlockInput],
    *,
    page_no: int,
    geometry: ColumnGeometry,
    main_x0: float,
    main_x1: float,
    column_count: int = 1,
    column_method: str = "single",
    baseline_zones: dict[str, str] | None = None,
) -> PageFeatures:
    """Measure every block against the page's main column and XY-cut bands."""
    geom = geometry
    zones = baseline_zones or {}

    boxed: list[tuple[BlockInput, Box]] = []
    for blk in blocks:
        box = _norm_box(blk.bbox)
        if box is not None:
            boxed.append((blk, box))
    all_boxes = [b for _blk, b in boxed]
    wide_boxes = [b for b in all_boxes if (b[2] - b[0]) >= geom.wide_min]

    cut = xy_cut(all_boxes, page_w=geom.page_w, min_gutter=geom.min_gutter)

    feats: list[BlockFeatures] = []
    for blk in blocks:
        box = _norm_box(blk.bbox)
        text = blk.text or ""
        if box is None:
            feats.append(
                BlockFeatures(
                    key=blk.key,
                    has_bbox=False,
                    baseline_zone=zones.get(blk.key, "main"),
                    zone_pinned=blk.zone_pinned,
                    type=blk.type,
                    mineru_type=blk.mineru_type,
                    role=blk.role,
                    text=text,
                    text_len=len(text),
                )
            )
            continue
        x0, y0, x1, y1 = box
        width = x1 - x0
        height = y1 - y0
        comp_l, comp_r, best_ov = _companions(box, all_boxes, geom)
        band_found, band_x0, band_x1 = _band_main_edges(box, wide_boxes, geom)
        feats.append(
            BlockFeatures(
                key=blk.key,
                has_bbox=True,
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                width=width,
                height=height,
                cx=(x0 + x1) / 2.0,
                cy=(y0 + y1) / 2.0,
                width_ratio=width / geom.page_w if geom.page_w else 0.0,
                is_wide=width >= geom.wide_min,
                is_full_width=width >= geom.page_w * geom.full_width_ratio,
                gutter_left=main_x0 - x1,
                gutter_right=x0 - main_x1,
                left_of_main=x1 <= main_x0 - geom.min_gutter,
                right_of_main=x0 >= main_x1 + geom.min_gutter,
                beyond_left_edge=x1 <= geom.aside_left_x1_max,
                beyond_right_edge=x0 >= geom.aside_right_x0_min,
                has_companion_left=comp_l,
                has_companion_right=comp_r,
                has_band_main=band_found,
                band_main_x0=band_x0,
                band_main_x1=band_x1,
                left_of_band_main=band_found and x1 + geom.min_gutter <= band_x0,
                right_of_band_main=band_found and x0 >= band_x1 + geom.min_gutter,
                max_y_overlap=best_ov,
                xycut_column=cut.column_of(x0, x1),
                baseline_zone=zones.get(blk.key, "main"),
                zone_pinned=blk.zone_pinned,
                type=blk.type,
                mineru_type=blk.mineru_type,
                role=blk.role,
                text=text,
                text_len=len(text),
            )
        )

    return PageFeatures(
        page_no=page_no,
        page_w=geom.page_w,
        main_x0=main_x0,
        main_x1=main_x1,
        column_count=column_count,
        column_method=column_method,
        xycut=cut,
        blocks=tuple(feats),
        page_block_count=len(feats),
        page_wide_count=sum(1 for f in feats if f.is_wide),
        page_text_len=sum(f.text_len for f in feats),
        page_figure_count=sum(1 for f in feats if f.type == "figure"),
        page_max_block_height=max((f.height for f in feats), default=0.0),
        geometry=geom,
    )
