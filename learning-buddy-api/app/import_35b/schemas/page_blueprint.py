"""PageBlueprint schema for the 35B-first import experiment.

V3 keeps the public PageBlueprint name but adds a stricter content contract:
each block has one primary ``content_type`` and one typed payload.  Legacy
fields are still accepted as migration input, but the repair/renderer path
normalizes them before HTML is emitted.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Zone = Literal["main", "aside_left", "aside_right", "full_bleed", "running"]
BlockKind = Literal["text", "figure", "table", "equation", "running"]
ContentType = Literal["text", "figure", "table", "equation", "running"]

_VALID_ROLES = frozenset(
    {
        "body",
        "running_header",
        "example",
        "exercise",
        "review",
        "explore",
        "reading",
        "summary",
        "definition",
        "note",
        "prompt",
        "symbol_note",
        "think",
        "figure",
        "figure_caption",
        "table",
        "toc",
        "equation",
    }
)


class TextPayload(BaseModel):
    paragraphs: list[str] = Field(default_factory=list)

    def plain_text(self) -> str:
        return "\n\n".join(str(p).strip() for p in self.paragraphs if str(p).strip())


class TablePayload(BaseModel):
    rows: list[list[str]] = Field(default_factory=list)
    caption: str | None = None

    def plain_text(self) -> str:
        cells = [str(cell).strip() for row in self.rows for cell in row if str(cell).strip()]
        if self.caption and self.caption.strip():
            cells.append(self.caption.strip())
        return " ".join(cells)


class EquationPayload(BaseModel):
    # ``latex`` is the single source of truth for rendering: the compiler emits
    # a MathJax span from it *only after* server-side validation. ``fallback_text``
    # is the human-readable plain-text form shown when the LaTeX cannot render —
    # it is never treated as LaTeX.
    latex: str | None = None
    fallback_text: str | None = None

    def plain_text(self) -> str:
        return " ".join(
            p.strip()
            for p in (self.latex, self.fallback_text)
            if isinstance(p, str) and p.strip()
        )


class FigurePayload(BaseModel):
    # A caption is a short label only (e.g. "图 1-1-1"). Body text must never
    # live here; the guided-decoding caption ``max_length`` enforces this.
    caption: str | None = None

    def plain_text(self) -> str:
        return (self.caption or "").strip()


class BlockContent(BaseModel):
    # Typed payloads. Only one should be active per block.
    text: str | TextPayload | None = None
    table: TablePayload | None = None
    equation: EquationPayload | None = None
    figure: FigurePayload | None = None

    # Legacy / pipeline-populated fields.  LLM-view V3 no longer exposes
    # table_html, caption, or latex as generic sibling fields.
    caption: str | None = None
    table_html: str | None = None
    latex: str | None = None
    asset_key: str | None = None
    asset_url: str | None = None
    text_segments: list[dict] | None = None

    def text_plain(self) -> str:
        if isinstance(self.text, TextPayload):
            return self.text.plain_text()
        return self.text.strip() if isinstance(self.text, str) and self.text.strip() else ""

    def text_paragraphs(self) -> list[str]:
        if isinstance(self.text, TextPayload):
            return [str(p).strip() for p in self.text.paragraphs if str(p).strip()]
        if isinstance(self.text, str) and self.text.strip():
            return [p.strip() for p in self.text.split("\n\n") if p.strip()]
        return []

    def table_rows(self) -> list[list[str]]:
        return self.table.rows if self.table else []

    def table_caption(self) -> str:
        return (self.table.caption or "").strip() if self.table and self.table.caption else ""

    def equation_latex(self) -> str:
        if self.equation and self.equation.latex:
            return self.equation.latex.strip()
        return (self.latex or "").strip()

    def equation_fallback_text(self) -> str:
        if self.equation and self.equation.fallback_text:
            return self.equation.fallback_text.strip()
        return self.text_plain()

    def figure_caption(self) -> str:
        if self.figure and self.figure.caption:
            return self.figure.caption.strip()
        return (self.caption or "").strip()

    def active_payloads(self) -> list[str]:
        active: list[str] = []
        if self.text_plain():
            active.append("text")
        if self.table and any(any(str(cell).strip() for cell in row) for row in self.table.rows):
            active.append("table")
        if self.equation and (self.equation.latex or self.equation.fallback_text):
            active.append("equation")
        if self.figure and self.figure.caption:
            active.append("figure")
        return active

    def plain_text(self) -> str:
        return " ".join(
            p.strip()
            for p in (
                self.text_plain(),
                self.table.plain_text() if self.table else "",
                self.equation.plain_text() if self.equation else "",
                self.figure.plain_text() if self.figure else "",
                self.caption,
                self.latex,
            )
            if isinstance(p, str) and p.strip()
        )


class BlueprintBlock(BaseModel):
    id: str
    kind: BlockKind
    content_type: ContentType | None = None
    zone: Zone = "main"
    role: str = "body"
    bbox: list[float] = Field(min_length=4, max_length=4)
    anchor_id: str | None = None
    figure_group_id: str | None = None
    content: BlockContent = Field(default_factory=BlockContent)

    @field_validator("id")
    @classmethod
    def non_empty_id(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("block id required")
        return value

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value) -> str:
        role = str(value or "body").strip().lower()
        return role if role in _VALID_ROLES else "body"

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float]) -> list[float]:
        x0, y0, x1, y1 = [float(v) for v in value[:4]]
        if x1 <= x0 or y1 <= y0:
            raise ValueError("bbox must satisfy x1>x0 and y1>y0")
        if min(x0, y0, x1, y1) < 0 or max(x0, y0, x1, y1) > 1000:
            raise ValueError("bbox must be normalized to 0..1000")
        return [x0, y0, x1, y1]

    @model_validator(mode="after")
    def align_kind_zone(self) -> "BlueprintBlock":
        if self.kind == "running" and self.zone != "running":
            self.zone = "running"
        if self.zone == "running" and self.kind != "running":
            self.kind = "running"

        if self.content_type is None:
            self.content_type = self.kind
        if self.content_type == "running":
            self.kind = "running"
            self.zone = "running"
        elif self.content_type in ("text", "figure", "table", "equation"):
            self.kind = self.content_type

        if self.kind == "figure" and self.role == "body":
            self.role = "figure"
        if self.kind == "equation" and self.role == "body":
            self.role = "equation"
        if self.kind == "table" and self.role == "body":
            self.role = "table"
        if self.kind == "running":
            self.content_type = "running"
        return self


class PageBlueprint(BaseModel):
    page_no: int = Field(ge=1)
    page_size: list[int] = Field(default_factory=lambda: [1000, 1000], min_length=2, max_length=2)
    blocks: list[BlueprintBlock] = Field(min_length=1)

    @field_validator("page_size")
    @classmethod
    def validate_page_size(cls, value: list[int]) -> list[int]:
        w, h = int(value[0]), int(value[1])
        if w <= 0 or h <= 0:
            raise ValueError("page_size must be positive")
        return [w, h]

    @model_validator(mode="after")
    def validate_page(self) -> "PageBlueprint":
        if not any(b.zone == "main" for b in self.blocks):
            raise ValueError("at least one main block is required")
        seen: set[str] = set()
        for block in self.blocks:
            if block.id in seen:
                raise ValueError(f"duplicate block id: {block.id}")
            seen.add(block.id)

        id_set = {b.id for b in self.blocks}
        cleaned: list[BlueprintBlock] = []
        for block in self.blocks:
            if block.anchor_id and block.anchor_id not in id_set:
                block = block.model_copy(update={"anchor_id": None})
            cleaned.append(block)
        self.blocks = cleaned
        return self

    def zone_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for block in self.blocks:
            counts[block.zone] = counts.get(block.zone, 0) + 1
        return counts

    def sorted_blocks(self) -> list[BlueprintBlock]:
        zone_order = {"aside_left": 0, "main": 1, "aside_right": 2, "full_bleed": 3, "running": 4}
        return sorted(
            self.blocks,
            key=lambda b: (zone_order.get(b.zone, 9), b.bbox[1], b.bbox[0], b.id),
        )

