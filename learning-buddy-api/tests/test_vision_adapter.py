"""Tests for Vision inline figure placement."""
from __future__ import annotations

from app.services.document_ir import DocumentBlock
from app.services.ir.vision_adapter import _inline_figures_after_references


def test_inline_figures_after_figure_reference():
    text_blocks = [
        DocumentBlock(
            id="p1",
            type="paragraph",
            page_no=12,
            order=1,
            text="闭区间与开区间在数轴上的表示，如图 1-1-1 所示。",
        ),
        DocumentBlock(
            id="p2",
            type="paragraph",
            page_no=12,
            order=2,
            text="半开半闭区间在数轴上的表示，如图 1-1-2 所示。",
        ),
    ]
    figures = [
        DocumentBlock(id="f1", type="figure", page_no=12, order=3, asset_url="/a1"),
        DocumentBlock(id="f2", type="figure", page_no=12, order=4, asset_url="/a2"),
        DocumentBlock(id="f3", type="figure", page_no=12, order=5, asset_url="/a3"),
        DocumentBlock(id="f4", type="figure", page_no=12, order=6, asset_url="/a4"),
    ]
    merged = _inline_figures_after_references(text_blocks, figures)
    assert merged[0].id == "p1"
    assert merged[1].type == "figure"
    assert merged[2].type == "figure"
    assert merged[3].id == "p2"
    assert merged[4].type == "figure"
