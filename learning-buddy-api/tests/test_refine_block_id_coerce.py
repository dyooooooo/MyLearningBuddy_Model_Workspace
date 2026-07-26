"""Block re-recognize must keep / coerce focus id (35B often renames)."""
from __future__ import annotations

from app.schemas.mineru_refine import PageRefineResult, RefineBlockPatch
from app.services.document_ir import DocumentBlock
from app.services.ir.mineru_page_refine import _coerce_focus_patches


def _target(bid: str = "c0d17d3acd38a116") -> DocumentBlock:
    return DocumentBlock(
        id=bid,
        type="paragraph",
        page_no=11,
        order=3,
        text="页眉内容",
        bbox=[31, 14, 115, 79],
    )


def test_coerce_exact_id():
    bid = "c0d17d3acd38a116"
    result = PageRefineResult(
        page_no=11,
        blocks=[RefineBlockPatch(id=bid, zone="running", text="1 集合")],
    )
    out = _coerce_focus_patches(result, block_id=bid, target=_target(bid))
    assert len(out) == 1
    assert out[0].id == bid


def test_coerce_sole_renamed_patch():
    bid = "c0d17d3acd38a116"
    result = PageRefineResult(
        page_no=11,
        blocks=[RefineBlockPatch(id="block_1", zone="running", text="1 集合")],
    )
    out = _coerce_focus_patches(result, block_id=bid, target=_target(bid))
    assert len(out) == 1
    assert out[0].id == bid
    assert out[0].text == "1 集合"


def test_coerce_sole_text_among_many():
    bid = "c0d17d3acd38a116"
    result = PageRefineResult(
        page_no=11,
        blocks=[
            RefineBlockPatch(id="a", zone="main"),
            RefineBlockPatch(id="b", zone="running", text="纠正后"),
            RefineBlockPatch(id="c", zone="main"),
        ],
    )
    out = _coerce_focus_patches(result, block_id=bid, target=_target(bid))
    assert len(out) == 1
    assert out[0].id == bid
    assert out[0].text == "纠正后"


def test_coerce_empty_when_ambiguous():
    bid = "c0d17d3acd38a116"
    result = PageRefineResult(
        page_no=11,
        blocks=[
            RefineBlockPatch(id="a", zone="main", text="x"),
            RefineBlockPatch(id="b", zone="main", text="y"),
        ],
    )
    out = _coerce_focus_patches(result, block_id=bid, target=_target(bid))
    assert out == []
