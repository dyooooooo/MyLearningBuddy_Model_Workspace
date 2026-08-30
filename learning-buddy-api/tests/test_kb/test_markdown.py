from __future__ import annotations

from app.kb.markdown import blocks_to_page_markdown, group_blocks_by_page


def test_group_and_markdown_with_figure():
    blocks = [
        {"type": "text", "page_idx": 0, "text": "集合初步"},
        {
            "type": "image",
            "page_idx": 1,
            "img_path": "/api/v1/kb/proxy/asset?key=kb%2Fpdfs%2Fb1%2Fassets%2Fmineru%2Ffig.png",
            "image_caption": "图 1-1",
        },
    ]
    grouped = group_blocks_by_page(blocks)
    assert list(grouped) == [1, 2]
    p1 = blocks_to_page_markdown(grouped[1])
    p2 = blocks_to_page_markdown(grouped[2])
    assert "集合初步" in p1
    assert "![图 1-1](/api/v1/kb/proxy/asset?key=" in p2
    assert "<div" not in p1


def test_caption_from_span_dict():
    md = blocks_to_page_markdown(
        [
            {
                "type": "image",
                "img_path": "/api/v1/kb/proxy/asset?key=kb%2Fpdfs%2Fb1%2Fx.png",
                "image_caption": [{"type": "text", "content": "图 1-1"}],
            }
        ]
    )
    assert "![图 1-1](" in md
    assert "{'type'" not in md


def test_equation_wrapped_as_display_math():
    md = blocks_to_page_markdown([{"type": "equation", "text": "A \\cup B"}])
    assert "$$" in md
    assert "A \\cup B" in md


def test_table_wrapped_with_caption_no_screenshot():
    md = blocks_to_page_markdown(
        [
            {
                "type": "table",
                "table_caption": [{"type": "text", "content": "表 1-1 常用数集的符号"}],
                "table_body": "<table><tr><td>数集</td><td>符号</td></tr><tr><td>自然数集</td><td>N</td></tr></table>",
                "img_path": "/api/v1/kb/proxy/asset?key=kb%2Fpdfs%2Fb1%2Fshot.jpg",
            }
        ]
    )
    assert 'class="kb-table-wrap"' in md
    assert "<caption>表 1-1 常用数集的符号</caption>" in md
    assert "<th" in md
    assert "shot.jpg" not in md


def test_table_caption_with_latex_backslash_does_not_crash():
    """Third-book import died here: re.sub treated \\mathrm as a bad escape."""
    md = blocks_to_page_markdown(
        [
            {
                "type": "table",
                "table_caption": "表 3-1 函数 $y=\\mathrm{e}^{x}$ 的值",
                "table_body": "<table><tr><td>x</td><td>y</td></tr></table>",
            }
        ]
    )
    assert "<caption>" in md
    assert "mathrm" in md
    assert 'class="kb-table-wrap"' in md


def test_index_block_becomes_page_toc():
    md = blocks_to_page_markdown(
        [
            {
                "type": "index",
                "content": {"list_items": ["第1章 集合与逻辑", "1.1 集合初步 2"]},
            }
        ],
        print_to_pdf={2: 9},
    )
    assert 'class="kb-page-toc"' in md
    assert "kb-page-toc-row--chapter" in md
    assert 'data-goto-print-page="2"' in md
    assert 'data-goto-page="9"' in md
