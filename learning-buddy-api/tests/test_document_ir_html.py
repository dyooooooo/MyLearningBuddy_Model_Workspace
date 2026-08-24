"""HTML render tests for document IR."""
import re

from app.services.document_ir import (
    DocumentBlock,
    html_to_plain_text,
    render_page_html,
    sanitize_page_html,
)


def test_render_page_html_structure():
    blocks = [
        DocumentBlock(
            id="m1",
            type="paragraph",
            page_no=12,
            order=1,
            text="主栏正文",
            bbox=[80, 200, 620, 400],
            meta={"layout_zone": "main", "pedagogy_role": "body", "reading_order": 1},
        ),
        DocumentBlock(
            id="a1",
            type="paragraph",
            page_no=12,
            order=2,
            text='符号"∞"读作"无穷大"',
            bbox=[720, 300, 900, 360],
            meta={
                "layout_zone": "aside_right",
                "pedagogy_role": "symbol_note",
                "anchor_block_id": "m1",
                "reading_order": 2,
            },
        ),
    ]
    html = render_page_html(blocks, 12)
    assert 'class="lb-page"' in html
    assert "lb-grid--r" in html
    assert "lb-aside--right" in html
    assert "lb-aside--left" not in html  # empty left column omitted
    assert "lb-main" in html
    assert "lb-col-placeholder" not in html
    assert "lb-callout" in html
    assert "data-anchor" in html
    assert "lb-aside-item" in html
    assert "--lb-gap:" in html
    assert "data-lb-gap=" in html
    # aside-item must stay balanced after sanitize
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    item = soup.select_one(".lb-aside-item")
    assert item is not None
    assert item.find_parent("aside") is not None
    assert soup.select_one('[data-zone="main"]') is not None
    assert soup.select_one('[data-zone="aside_right"]') is not None
    # main and aside are siblings under grid (not stacked via nesting)
    grid = soup.select_one(".lb-grid")
    kids = [c for c in grid.children if getattr(c, "name", None)]
    assert [c.get("data-zone") for c in kids] == ["main", "aside_right"]


def test_render_paragraph_inline_math():
    blocks = [
        DocumentBlock(
            id="p1",
            type="paragraph",
            page_no=16,
            order=1,
            text=r"若 $a \leqslant x < b$，则 $x \in A$。",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 16)
    assert "data-latex" in html
    assert "math--inline" in html
    assert "a \\leqslant x &lt; b" in html


def test_render_bare_latex_set():
    blocks = [
        DocumentBlock(
            id="e1",
            type="paragraph",
            page_no=16,
            order=1,
            text=r"A = \{(x, y) | 2x + y = 5 \}",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 16)
    assert "data-latex" in html
    assert r"\{" in html


def test_mixed_cjk_bare_latex_not_whole_math_span():
    """Chinese + ``\\leqslant`` must stay prose+inline math, not one formula span."""
    blocks = [
        DocumentBlock(
            id="i4",
            type="paragraph",
            page_no=9,
            order=1,
            text=r"（4）不等式2 x + 1 { \leqslant } 0的所有解；",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 9)
    assert 'class="lb-list-item"' in html
    assert "（4）不等式" in html or ">（4）<" in html or "）不等式" in html
    # Must NOT wrap the whole sentence (incl. Chinese) as one data-latex
    assert "不等式2 x + 1" not in html.split("data-latex")[1].split('"')[1] if "data-latex" in html else True
    latex_attrs = re.findall(r'data-latex="([^"]*)"', html)
    assert latex_attrs, "expected an inline math span"
    for latex in latex_attrs:
        assert "不等式" not in latex
        assert "所有解" not in latex
    assert "的所有解" in html


def test_glued_numbered_items_with_inline_math_split():
    """MinerU often glues （3）…(4)…; must become two list lines, not one math span."""
    blocks = [
        DocumentBlock(
            id="g1",
            type="list",
            page_no=10,
            order=1,
            text=r"（3）{ \sqrt { 2 } } .Q; (4)- \sqrt { \pi } .R.",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 10)
    assert html.count("lb-list-item") >= 2
    assert "（3）" in html
    assert "（4）" in html
    # Must not put both markers inside one data-latex
    for latex in re.findall(r'data-latex="([^"]*)"', html):
        assert not ("（3）" in latex and "(4)" in latex)
        assert not ("（3）" in latex and "（4）" in latex)


def test_solution_lead_splits_glued_answers():
    blocks = [
        DocumentBlock(
            id="s",
            type="paragraph",
            page_no=10,
            order=1,
            text=r"解 (1) 0∈N. (2)1 \in Z. (3)\sqrt{2}\notin Q.",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 10)
    assert "lb-solution-lead" in html
    assert "lb-solution-item" in html
    assert html.count("lb-solution-item") >= 2


def test_solution_lead_indent_layout():
    blocks = [
        DocumentBlock(
            id="q2",
            type="paragraph",
            page_no=10,
            order=1,
            text="（2）600的正约数的全体组成的集合；",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
        DocumentBlock(
            id="s1",
            type="paragraph",
            page_no=10,
            order=2,
            text="解（1）6的正整数倍可表示为6n，其中n是正整数．",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
        DocumentBlock(
            id="s2",
            type="paragraph",
            page_no=10,
            order=3,
            text="（2）600的正约数一定是小于或等于600的正整数．",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
        DocumentBlock(
            id="body",
            type="paragraph",
            page_no=10,
            order=4,
            text="数学中，常常需要用到数的集合．",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 10)
    assert "lb-solution-lead" in html
    assert 'class="lb-solution-label">解</span>' in html or ">解</span>" in html
    assert "（1）6的正整数倍" in html
    assert "lb-solution-item" in html
    assert "lb-list-item" in html  # question keeps list indent
    assert html.index("lb-solution-lead") < html.index("数学中")
    assert "lb-solution-lead" in html


def test_html_to_plain_text():
    html = "<article><p>集合</p><aside>思考</aside></article>"
    plain = html_to_plain_text(html)
    assert "集合" in plain
    assert "思考" in plain


def test_render_page_header_and_number():
    blocks = [
        DocumentBlock(
            id="h1",
            type="paragraph",
            page_no=1,
            order=1,
            text="前言",
            bbox=[848, 36, 882, 51],
            meta={
                "layout_zone": "running",
                "pedagogy_role": "running_header",
                "mineru_type": "page_header",
                "skip_render": True,
            },
        ),
        DocumentBlock(
            id="n1",
            type="paragraph",
            page_no=1,
            order=2,
            text="1",
            bbox=[889, 960, 904, 985],
            meta={
                "layout_zone": "running",
                "pedagogy_role": "page_number",
                "mineru_type": "page_number",
                "skip_render": True,
            },
        ),
        DocumentBlock(
            id="b1",
            type="paragraph",
            page_no=1,
            order=3,
            text="正文段落",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 1)
    assert '<header class="lb-running-hd">' in html
    assert '<span class="lb-chrome-hit" data-block-id="h1">前言</span>' in html
    assert "lb-page-number" in html
    assert ">1</span>" in html
    assert 'data-print-page="1"' in html
    assert "正文段落" in html
    assert "lb-running-ft-row" in html


def test_multiple_page_headers_all_kept():
    blocks = [
        DocumentBlock(
            id="h1",
            type="paragraph",
            page_no=8,
            order=1,
            text="1",
            bbox=[31, 14, 114, 80],
            meta={
                "layout_zone": "running",
                "pedagogy_role": "running_header",
                "mineru_type": "page_header",
                "skip_render": True,
            },
        ),
        DocumentBlock(
            id="h2",
            type="paragraph",
            page_no=8,
            order=2,
            text="集合与逻辑",
            bbox=[126, 35, 208, 51],
            meta={
                "layout_zone": "running",
                "pedagogy_role": "running_header",
                "mineru_type": "page_header",
                "skip_render": True,
            },
        ),
        DocumentBlock(
            id="b1",
            type="paragraph",
            page_no=8,
            order=3,
            text="正文",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 8)
    assert '<header class="lb-running-hd">' in html
    assert '<span class="lb-chrome-hit" data-block-id="h1">1</span>' in html
    assert '<span class="lb-chrome-hit" data-block-id="h2">集合与逻辑</span>' in html
    assert "lb-running-hd--split" not in html


def test_render_index_toc_clickable():
    blocks = [
        DocumentBlock(
            id="toc1",
            type="list",
            page_no=6,
            order=1,
            text="第1章 集合与逻辑\n1.1 集合初步 2",
            meta={
                "layout_zone": "main",
                "pedagogy_role": "toc",
                "mineru_type": "index",
                "toc_entries": [
                    {"label": "第1章 集合与逻辑", "print_page": None, "is_chapter": True},
                    {"label": "1.1 集合初步", "print_page": 2, "is_chapter": False},
                ],
            },
        ),
        DocumentBlock(
            id="pn2",
            type="paragraph",
            page_no=9,
            order=1,
            text="2",
            meta={
                "layout_zone": "running",
                "pedagogy_role": "page_number",
                "mineru_type": "page_number",
                "skip_render": True,
            },
        ),
    ]
    html = render_page_html(blocks, 6)
    assert 'class="lb-toc"' in html
    assert "第1章 集合与逻辑" in html
    # Whole row carries jump attrs; page number is a right-aligned span
    assert 'lb-toc-row--link' in html
    assert 'data-goto-print-page="2"' in html
    assert 'data-goto-page="9"' in html
    assert '<span class="lb-toc-page">2</span>' in html
    assert 'lb-toc-leader' in html
    assert "lb-toc-row--lv1" in html
    assert "lb-toc-row--lv0" in html or "lb-toc-row--chapter" in html
    assert "<a " not in html.split("lb-toc")[1].split("</nav>")[0]


def test_numbered_list_items_align_no_indent():
    from app.services.document_ir import (
        normalize_block_text,
        normalize_numbered_list_marker,
        polish_page_html,
    )

    assert normalize_block_text(" 　(1）foo  ") == "(1）foo"
    assert normalize_numbered_list_marker("(1）申辉") == "（1）申辉"
    assert normalize_numbered_list_marker("（2）自然数") == "（2）自然数"
    assert normalize_numbered_list_marker("(4) 不等式") == "（4）不等式"

    blocks = [
        DocumentBlock(
            id="intro",
            type="paragraph",
            page_no=9,
            order=1,
            text="例如：",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
        DocumentBlock(
            id="i1",
            type="paragraph",
            page_no=9,
            order=2,
            text="(1）申辉中学高一(1)班的全体学生；",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
        DocumentBlock(
            id="i2",
            type="paragraph",
            page_no=9,
            order=3,
            text="（2）所有不大于100的自然数；",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 9)
    assert 'class="lb-list-item"' in html
    assert "（1）申辉" in html
    assert "（2）所有" in html
    assert "(1）申辉" not in html

    polished = polish_page_html(
        '<div class="lb-block"><p>(1）申辉；</p></div>'
        '<div class="lb-block"><p>  （2）自然数；</p></div>'
    )
    assert 'class="lb-list-item"' in polished
    assert "（1）申辉" in polished
    assert "（2）自然数" in polished


def test_sanitize_keeps_figure():
    raw = '<figure class="lb-fig-group"><img src="/x.jpg" alt="图" /></figure>'
    clean = sanitize_page_html(raw)
    assert "figure" in clean
    assert "img" in clean


def test_figure_group_cell_caps_and_shared_caption():
    """2×2 panel group: sub-labels under cells; 图 X-X as figcaption (not last panel)."""
    gid = "fig-6-1-8"
    blocks = [
        DocumentBlock(
            id="p1",
            type="paragraph",
            page_no=16,
            order=1,
            text="上表的结果可用图 6-1-8 直观表示。",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
        DocumentBlock(
            id="f1",
            type="figure",
            page_no=16,
            order=2,
            bbox=[243.0, 129.0, 361.0, 214.0],
            asset_url="/api/v1/studio/proxy/import/asset?key=a.jpg",
            caption="sin α",
            meta={
                "layout_zone": "main",
                "figure_group_id": gid,
                "figure_group_caption": "图 6-1-8",
            },
        ),
        DocumentBlock(
            id="f2",
            type="figure",
            page_no=16,
            order=3,
            bbox=[394.0, 130.0, 514.0, 214.0],
            asset_url="/api/v1/studio/proxy/import/asset?key=b.jpg",
            caption="cos α",
            meta={"layout_zone": "main", "figure_group_id": gid},
        ),
        DocumentBlock(
            id="f3",
            type="figure",
            page_no=16,
            order=4,
            bbox=[243.0, 252.0, 361.0, 336.0],
            asset_url="/api/v1/studio/proxy/import/asset?key=c.jpg",
            caption="tan α",
            meta={"layout_zone": "main", "figure_group_id": gid},
        ),
        DocumentBlock(
            id="f4",
            type="figure",
            page_no=16,
            order=5,
            bbox=[396.0, 252.0, 514.0, 336.0],
            asset_url="/api/v1/studio/proxy/import/asset?key=d.jpg",
            caption="cot α",
            meta={"layout_zone": "main", "figure_group_id": gid},
        ),
    ]
    html = render_page_html(blocks, 16)
    assert html.count('class="lb-fig-group"') == 1
    assert html.count('class="lb-fig-row"') == 2
    assert 'class="lb-fig-cell-cap">sin α</div>' in html
    assert 'class="lb-fig-cell-cap">cos α</div>' in html
    assert 'class="lb-fig-cell-cap">tan α</div>' in html
    assert 'class="lb-fig-cell-cap">cot α</div>' in html
    assert "<figcaption>图 6-1-8</figcaption>" in html
    assert "<figcaption>cos α</figcaption>" not in html
    assert "<figcaption>cot α</figcaption>" not in html


def test_page_number_links_back_to_toc():
    blocks = [
        DocumentBlock(
            id="toc1",
            type="list",
            page_no=6,
            order=1,
            text="目录",
            meta={"layout_zone": "main", "pedagogy_role": "toc", "mineru_type": "index"},
        ),
        DocumentBlock(
            id="pn9",
            type="paragraph",
            page_no=9,
            order=1,
            text="2",
            meta={
                "layout_zone": "running",
                "pedagogy_role": "page_number",
                "mineru_type": "page_number",
                "skip_render": True,
            },
        ),
        DocumentBlock(
            id="body9",
            type="paragraph",
            page_no=9,
            order=2,
            text="正文",
            meta={"layout_zone": "main", "pedagogy_role": "body"},
        ),
    ]
    html = render_page_html(blocks, 9)
    assert 'lb-page-number--toc-link' in html
    assert 'title="返回目录页"' in html
    assert 'data-goto-page="6"' in html
    # TOC page itself should not link to itself
    toc_html = render_page_html(blocks, 6)
    assert "lb-page-number--toc-link" not in toc_html
