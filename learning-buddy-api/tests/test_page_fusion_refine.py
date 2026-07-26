"""Unit tests for page fusion align / fuse / content_only apply / chrome hits."""
from __future__ import annotations

from app.schemas.mineru_refine import PageRefineResult, RefineBlockPatch
from app.services.document_ir import DocumentBlock, render_page_html
from app.services.ir.apply_refine import apply_page_refine, count_effective_changes
from app.services.ir.page_fusion.align import align_units
from app.services.ir.page_fusion.fuse import fuse_page_blocks
from app.services.ir.page_fusion.schemas import AlignedUnit, JudgeDecision, PageUnit
from app.services.ir.page_fusion.units import blocks_to_units


def test_align_units_pairs_by_iou():
    m = [
        PageUnit(
            id="m1",
            type="paragraph",
            zone="main",
            bbox=[100, 100, 400, 160],
            text="hello",
            source="mineru",
            block_id="b1",
        )
    ]
    b = [
        PageUnit(
            id="u1",
            type="paragraph",
            zone="main",
            bbox=[110, 105, 390, 155],
            text="hello!",
            source="35b",
        )
    ]
    aligned = align_units(m, b)
    assert len(aligned) == 1
    assert aligned[0].kind == "paired"


def test_fuse_drop_and_keep():
    aligned = [
        AlignedUnit(
            unit_id="a1",
            kind="paired",
            mineru=PageUnit(
                id="m1",
                type="paragraph",
                zone="running",
                pedagogy_role="running_header",
                text="1 集合与逻辑",
                source="mineru",
                block_id="h1",
            ),
            b35=PageUnit(
                id="u1",
                type="paragraph",
                zone="running",
                pedagogy_role="running_header",
                text="集合与逻辑",
                source="35b",
            ),
        ),
        AlignedUnit(
            unit_id="a2",
            kind="mineru_only",
            mineru=PageUnit(
                id="m2",
                type="paragraph",
                zone="running",
                pedagogy_role="running_header",
                text="集合与逻辑",
                source="mineru",
                block_id="h2",
            ),
        ),
    ]
    decisions = [
        JudgeDecision(unit_id="a1", action="keep_35b"),
        JudgeDecision(unit_id="a2", action="drop"),
    ]
    fused = fuse_page_blocks(page_no=11, aligned=aligned, decisions=decisions)
    assert len(fused) == 1
    assert fused[0].text == "集合与逻辑"
    # Paired keep_35b preserves MinerU block id
    assert fused[0].id == "h1"
    assert fused[0].meta.get("fusion_source") == "35b"


def test_fuse_allows_drop_mineru_only_body():
    """With MinerU baseline rollback, judge drop is honored (no safety override)."""
    aligned = [
        AlignedUnit(
            unit_id="a10",
            kind="mineru_only",
            mineru=PageUnit(
                id="m10",
                type="list",
                zone="main",
                pedagogy_role="body",
                text="（1）所有不大于 10 的正整数组成的集合；",
                source="mineru",
                block_id="02d49e7bf9ea54f4",
            ),
        )
    ]
    decisions = [JudgeDecision(unit_id="a10", action="drop")]
    fused = fuse_page_blocks(page_no=11, aligned=aligned, decisions=decisions)
    assert fused == []


def test_content_only_freezes_zone_role():
    before = DocumentBlock(
        id="x1",
        type="paragraph",
        page_no=1,
        order=1,
        text="old",
        meta={"layout_zone": "main", "pedagogy_role": "body"},
    )
    result = PageRefineResult(
        page_no=1,
        blocks=[
            RefineBlockPatch(
                id="x1",
                zone="aside_right",
                pedagogy_role="note",
                text="new text",
            )
        ],
    )
    after = apply_page_refine([before], result, content_only=True)
    assert after[0].text == "new text"
    assert after[0].meta.get("layout_zone") == "main"
    assert after[0].meta.get("pedagogy_role") == "body"
    assert count_effective_changes([before], after) == 1


def test_chrome_html_has_data_block_id():
    blocks = [
        DocumentBlock(
            id="hdr1",
            type="paragraph",
            page_no=1,
            order=1,
            text="集合与逻辑",
            bbox=[100, 20, 400, 50],
            meta={
                "layout_zone": "running",
                "pedagogy_role": "running_header",
                "skip_render": True,
            },
        ),
        DocumentBlock(
            id="pn1",
            type="paragraph",
            page_no=1,
            order=2,
            text="4",
            bbox=[50, 900, 80, 930],
            meta={
                "layout_zone": "running",
                "pedagogy_role": "page_number",
                "skip_render": True,
                "chrome_slot": "aside_left",
            },
        ),
        DocumentBlock(
            id="body1",
            type="paragraph",
            page_no=1,
            order=3,
            text="正文段落",
            bbox=[100, 200, 700, 260],
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 1)
    assert 'data-block-id="hdr1"' in html
    assert 'data-block-id="pn1"' in html
    assert "lb-chrome-hit" in html
    assert 'data-block-id="body1"' in html


def test_blocks_to_units_includes_running():
    blocks = [
        DocumentBlock(
            id="r1",
            type="paragraph",
            page_no=2,
            order=1,
            text="页眉",
            meta={"layout_zone": "running", "pedagogy_role": "running_header"},
        )
    ]
    units = blocks_to_units(blocks, 2)
    assert len(units) == 1
    assert units[0].zone == "running"
    assert units[0].block_id == "r1"
