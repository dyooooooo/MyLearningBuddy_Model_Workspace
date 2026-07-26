"""Unit tests for DocumentBlock IR rendering and helpers."""
from __future__ import annotations

from app.services.document_ir import (
    DocumentBlock,
    is_chapter_text,
    is_lesson_text,
    normalize_chapter_title,
    normalize_figure_caption,
    render_block_markdown,
    render_document_markdown,
    render_page_markdown,
)


def test_chapter_and_lesson_detection():
    assert is_chapter_text("第一章 集合")
    assert is_chapter_text("# 第二章 函数")
    assert is_lesson_text("1.1 集合初步")
    assert is_lesson_text("## 1.2 集合间的基本关系")


def test_render_table_and_figure():
    table = DocumentBlock(
        id="t1",
        type="table",
        page_no=1,
        order=1,
        table_html="<table><tr><td>表1-1</td></tr></table>",
        caption="表 1-1",
    )
    md = render_block_markdown(table)
    assert "<table" in md
    assert "表 1-1" in md

    fig = DocumentBlock(
        id="f1",
        type="figure",
        page_no=1,
        order=2,
        asset_url="/api/v1/studio/proxy/import/asset?key=corpus%2Ftest.jpg",
        caption="插图",
    )
    fig_md = render_block_markdown(fig)
    assert "![插图]" in fig_md
    assert "import/asset" in fig_md


def test_render_page_and_document():
    blocks = [
        DocumentBlock(id="a", type="paragraph", page_no=1, order=1, text="Page one"),
        DocumentBlock(id="b", type="paragraph", page_no=2, order=2, text="Page two"),
    ]
    assert "Page one" in render_page_markdown(blocks, 1)
    assert "Page two" not in render_page_markdown(blocks, 1)
    doc = render_document_markdown(blocks)
    assert "Page one" in doc
    assert "---" in doc
    assert "Page two" in doc


def test_normalize_figure_caption_drops_interval_noise():
    assert normalize_figure_caption("$[a,b]$") is None
    assert normalize_figure_caption("(a,b)\n图1-1-1") == "图1-1-1"
    assert normalize_figure_caption("图 1-1-2") == "图 1-1-2"


def test_normalize_chapter_title_first_line_only():
    blob = "第1章 集合与逻辑\n1.1 集合初步 2\n1.2 常用逻辑用语 13"
    assert normalize_chapter_title(blob) == "第1章 集合与逻辑"


def test_render_figure_no_duplicate_interval():
    block = DocumentBlock(
        id="f1",
        type="figure",
        page_no=12,
        order=1,
        asset_url="/api/v1/studio/proxy/import/asset?key=x",
        caption="$[a,b]$",
    )
    md = render_block_markdown(block)
    assert "$[a,b]$" not in md.split("![")[0]
    assert "![插图]" in md

    block2 = block.model_copy(update={"caption": "(a,b)\n图1-1-1"})
    md2 = render_block_markdown(block2)
    assert "图1-1-1" in md2
    assert "(a,b)" not in md2.replace("![图1-1-1]", "")
    b = DocumentBlock(
        id="x",
        type="paragraph",
        page_no=1,
        order=1,
        text="abc",
        caption="cap",
    )
    assert b.content_length() == 6
