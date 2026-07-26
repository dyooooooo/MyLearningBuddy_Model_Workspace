"""Edition Bundle schema — textbook-agnostic data contracts.

Code knows only these shapes. Textbook-specific content lives in bundle data.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Bounded facet vocabulary — Prompt Slots request subsets of these.
PolicyFacet = Literal[
    "layout_geometry",
    "zone_hints",
    "running_chrome",
    "aside_policy",
    "toc_style",
    "figure_policy",
    "math_density",
    "pedagogy_roles",
    "content_correct",
    "layout_rebuild",
    "unit_judge",
    "vision_layout",
]

PolicySeverity = Literal["must", "should", "may"]


class PolicyEntry(BaseModel):
    id: str
    facet: PolicyFacet
    severity: PolicySeverity = "should"
    statement: str
    # Optional tags for filtering / ops — never pipeline mode names.
    tags: list[str] = Field(default_factory=list)


class PolicyDocument(BaseModel):
    version: str = "1"
    entries: list[PolicyEntry] = Field(default_factory=list)


class GeometryConfig(BaseModel):
    """Named parameters for column/aside engines (no textbook names)."""

    page_w: float = 1000.0
    wide_min: float = 380.0
    aside_max_width: float = 320.0
    min_gutter: float = 18.0
    full_width_ratio: float = 0.72
    min_y_overlap: float = 18.0
    min_companion_width: float = 200.0
    aside_left_x1_max: float = 280.0
    aside_right_x0_min: float = 680.0
    figure_y_tolerance: float = 35.0


class PatternConfig(BaseModel):
    """Regex patterns used by adapters / document_ir — data, not code."""

    chapter: str = r"^第[０-９0-9一二三四五六七八九十百千]+章\s*.+"
    lesson: str = r"^[０-９0-9]+[\.．][０-９0-9]+\s+\S"
    fig_label: str = r"^图[\s　]*[０-９0-9]+[\.．-][０-９0-9\.．-]*"
    fig_ref: str = r"如图\s*[\d\.．-]+|图\s*[\d\.．-]+\s*所示"
    solution_lead: str = r"^解\s*$|^解\s*([（(][0-9０-９].*)$"
    toc_trailing_page: str = r"^(.+?)\s+(\d+)\s*$"
    chapter_toc: str = r"^第\s*[0-9０-９一二三四五六七八九十百]+\s*章"
    toc_section: str = r"^\\?\*?(\d+(?:\.\d+)+)\b"
    toc_summary: str = r"^(内容提要|复习题|习题|小结)"
    toc_titles: list[str] = Field(default_factory=lambda: ["目录", "目 录"])
    pedagogy_patterns: dict[str, str] = Field(default_factory=dict)
    figure_caption: str | None = None


class FeaturesConfig(BaseModel):
    columns: str = "single"
    running_header_footer: bool = True
    aside: str = "none"
    toc_style: str = "text_list"
    figure_policy: str = "strict_label"
    math_density: str = "medium"
    # Early pages treated as cover/back-cover: full-page scan, no OCR text.
    cover_pages: list[int] = Field(default_factory=lambda: [1, 2])


class EditionConfig(BaseModel):
    refine_mode: str = "skip"
    refine_parallel: int = 1
    geometry: GeometryConfig = Field(default_factory=GeometryConfig)
    patterns: PatternConfig = Field(default_factory=PatternConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    # Legacy flat keys kept for back-compat with layout_enrich consumers.
    zones: dict[str, Any] = Field(default_factory=dict)
    figure_group: dict[str, Any] = Field(default_factory=dict)


class EditionMeta(BaseModel):
    id: str
    version: str = "1"
    display_name: str = ""
    base: str | None = "_base"
    subject: str = "math"
    grade_band: str = "hs"
    description: str = ""


class EditionAssets(BaseModel):
    theme_css: str = "theme.css"  # relative path under assets/
    glyph_map: dict[str, str] = Field(default_factory=dict)


class EditionBundle(BaseModel):
    meta: EditionMeta
    config: EditionConfig = Field(default_factory=EditionConfig)
    policy: PolicyDocument = Field(default_factory=PolicyDocument)
    assets: EditionAssets = Field(default_factory=EditionAssets)

    def profile_compat_dict(self) -> dict[str, Any]:
        """Legacy textbook_profile-shaped dict for existing callers."""
        geom = self.config.geometry
        zones = dict(self.config.zones) if self.config.zones else {
            "aside_left_x1_max": geom.aside_left_x1_max,
            "aside_right_x0_min": geom.aside_right_x0_min,
        }
        fig = dict(self.config.figure_group) if self.config.figure_group else {
            "y_tolerance": geom.figure_y_tolerance,
        }
        if self.config.patterns.figure_caption:
            fig["caption_pattern"] = self.config.patterns.figure_caption
        return {
            "profile_id": self.meta.id,
            "edition_id": self.meta.id,
            "edition_version": self.meta.version,
            "refine_mode": self.config.refine_mode,
            "refine_parallel": self.config.refine_parallel,
            "zones": zones,
            "features": self.config.features.model_dump(),
            "pedagogy_patterns": dict(self.config.patterns.pedagogy_patterns),
            "figure_group": fig,
            "geometry": geom.model_dump(),
            "patterns": self.config.patterns.model_dump(),
        }
