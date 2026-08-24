"""Edition geometry helpers for column_detect engines."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.editions.bundle import load_bundle
from app.services.editions.schema import GeometryConfig


@dataclass(frozen=True)
class ColumnGeometry:
    page_w: float = 1000.0
    wide_min: float = 380.0
    aside_max_width: float = 320.0
    min_gutter: float = 18.0
    full_width_ratio: float = 0.72
    min_y_overlap: float = 18.0
    min_companion_width: float = 200.0
    aside_left_x1_max: float = 280.0
    aside_right_x0_min: float = 680.0
    # Half-height of the vertical window used for band-local companion checks.
    # Pages that are two-column at the top and single-column at the bottom have
    # no single page-wide main edge; band-local features look at neighbours
    # within this window instead.
    band_pad_min: float = 60.0


def geometry_from_edition(edition_id: str | None) -> ColumnGeometry:
    if not edition_id:
        return ColumnGeometry()
    try:
        bundle = load_bundle(edition_id)
    except (FileNotFoundError, ValueError):
        return ColumnGeometry()
    g: GeometryConfig = bundle.config.geometry
    zones = bundle.config.zones or {}
    return ColumnGeometry(
        page_w=g.page_w,
        wide_min=g.wide_min,
        aside_max_width=g.aside_max_width,
        min_gutter=g.min_gutter,
        full_width_ratio=g.full_width_ratio,
        min_y_overlap=g.min_y_overlap,
        min_companion_width=g.min_companion_width,
        aside_left_x1_max=float(zones.get("aside_left_x1_max", g.aside_left_x1_max)),
        aside_right_x0_min=float(zones.get("aside_right_x0_min", g.aside_right_x0_min)),
    )
