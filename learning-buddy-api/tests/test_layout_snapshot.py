"""Frozen page HTML for representative layouts (syrupy snapshots).

These pages go through the real path — enrich (geometry + rules) then render —
so a change to geometry, rule data, or a block renderer shows up as an HTML
diff instead of only being visible by eyeballing the Studio preview.

Refresh deliberately after reviewing the diff:

    pytest tests/test_layout_snapshot.py --snapshot-update
"""
from __future__ import annotations

import pytest

from app.services.document_ir import DocumentBlock, render_page_html
from app.services.ir.layout_enrich import enrich_blocks

HUJIAO = "hujiao_math_hs_v1"


def _render(blocks: list[DocumentBlock], page_no: int) -> str:
    enriched = enrich_blocks(blocks, profile_id=HUJIAO)
    return render_page_html(enriched, page_no, profile_id=HUJIAO)


def _para(bid: str, page: int, order: int, bbox, text: str, **meta) -> DocumentBlock:
    return DocumentBlock(
        id=bid,
        type="paragraph",
        page_no=page,
        order=order,
        bbox=bbox,
        text=text,
        meta={"mineru_type": "text", **meta},
    )


def test_snapshot_single_column_body(snapshot):
    blocks = [
        _para("p1", 40, 1, [89, 100, 667, 200], "集合是数学中最基本的概念之一."),
        _para("p2", 40, 2, [89, 220, 667, 400], "不含有任何元素的集合称为空集，记作∅."),
        _para("p3", 40, 3, [90, 420, 667, 600], "引进空集是有必要的."),
    ]
    assert _render(blocks, 40) == snapshot


def test_snapshot_right_margin_note_rescued(snapshot):
    """The short right note baseline geometry drops; the edition rule keeps it."""
    blocks = [
        _para("p1", 41, 1, [80, 100, 600, 200], "命题的否定与反证法."),
        _para("p2", 41, 2, [80, 220, 600, 400], "若 p 则 q 的逆否命题与原命题等价."),
        _para("p3", 41, 3, [80, 420, 600, 600], "由此可得证明思路."),
        _para(
            "tip",
            41,
            4,
            [640, 300, 790, 325],
            "提示：先假设结论不成立.",
            pedagogy_role="prompt",
        ),
    ]
    assert _render(blocks, 41) == snapshot


def test_snapshot_left_aside_with_figure(snapshot):
    blocks = [
        DocumentBlock(
            id="fig",
            type="figure",
            page_no=42,
            order=1,
            bbox=[161, 302, 260, 412],
            caption="康托(G. Cantor，1845—1918)，德国数学家.",
            asset_url="/api/v1/studio/proxy/import/asset?key=cantor.jpg",
            meta={"mineru_type": "image"},
        ),
        DocumentBlock(
            id="t1",
            type="title",
            page_no=42,
            order=2,
            bbox=[329, 100, 800, 160],
            text="1.1 集合初步",
            meta={"mineru_type": "title"},
        ),
        _para("p1", 42, 3, [329, 200, 909, 400], "我们经常需要把满足一定要求的对象放在一起."),
        _para(
            "pn",
            42,
            4,
            [40, 950, 70, 980],
            "2",
            mineru_type="page_number",
            pedagogy_role="page_number",
        ),
    ]
    assert _render(blocks, 42) == snapshot


def test_snapshot_chapter_opener_keeps_title_in_main(snapshot):
    blocks = [
        DocumentBlock(
            id="t1",
            type="title",
            page_no=43,
            order=1,
            bbox=[120, 200, 300, 280],
            text="第二章 等式与不等式",
            meta={"mineru_type": "title"},
        ),
        _para("p1", 43, 2, [400, 210, 860, 270], "本章学习等式与不等式的基本性质."),
    ]
    assert _render(blocks, 43) == snapshot


def test_snapshot_figure_group_two_rows(snapshot):
    gid = "grp-1"
    blocks = [
        DocumentBlock(
            id=f"f{i}",
            type="figure",
            page_no=44,
            order=i,
            bbox=bbox,
            caption=cap,
            asset_url=f"/api/v1/studio/proxy/import/asset?key={i}.jpg",
            meta={"mineru_type": "image", "figure_group_id": gid},
        )
        for i, (bbox, cap) in enumerate(
            [
                ([243, 130, 361, 214], "sin α"),
                ([394, 130, 514, 214], "cos α"),
                ([243, 252, 361, 336], "tan α"),
                ([396, 252, 514, 336], "cot α"),
            ],
            start=1,
        )
    ]
    blocks.append(_para("p1", 44, 5, [89, 400, 667, 520], "四个三角函数的图像如上."))
    assert _render(blocks, 44) == snapshot


@pytest.mark.parametrize("page_no", [40, 41, 42, 43, 44])
def test_snapshot_pages_are_single_article(page_no):
    """Guard the page skeleton contract that every snapshot above relies on."""
    blocks = [_para("p1", page_no, 1, [89, 100, 667, 200], "占位正文.")]
    html = _render(blocks, page_no)
    assert html.count("<article") == 1
    assert html.count('<div class="lb-grid') == 1
