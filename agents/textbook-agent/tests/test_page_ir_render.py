"""page-ir 渲染器（render-page.mjs）的契约测试。

这个渲染器之前一行测试都没有，titleHtml 只读 blk.text 把标题里的公式、下标、
制表位全压成转义字面量的 bug 就是这么活下来的。这里用 node 直接调 renderIrPage。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_LIB = (
    Path(__file__).resolve().parents[1]
    / "workspace"
    / "surfaces"
    / "page-ir"
    / "lib"
    / "render-page.mjs"
)

_DRIVER = """
const fs = require("fs");
(async () => {
  const { renderIrPage } = await import(process.env.LIB);
  const page = JSON.parse(fs.readFileSync(process.env.PAGE, "utf8"));
  process.stdout.write(String(renderIrPage(page, { bookId: "bk" })));
})();
"""


def _render(page: dict, tmp_path: Path) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("这台机器没有 node")
    page_file = tmp_path / "page.json"
    page_file.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [node, "-e", _DRIVER],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin",
            "LIB": _LIB.as_uri(),
            "PAGE": str(page_file),
        },
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _title_page(text: str, lines: list | None) -> dict:
    blk: dict = {"id": "kb-t", "type": "title", "level": 2, "text": text}
    if lines is not None:
        blk["lines"] = lines
    return {
        "blocks": [blk],
        "layout": {
            "tree": {
                "id": "page",
                "kind": "rows",
                "members": [0],
                "children": [{"id": "r0", "kind": "flow", "members": [0]}],
            }
        },
    }


def test_inline_math_is_chtml_not_image(tmp_path: Path) -> None:
    """行内公式必须是 LaTeX 排出的网页字，不能是 svg/jpg 图。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "ex1",
                "type": "text",
                "kind": "body",
                "bbox": [221.0, 108.0, 326.0, 123.0],
                "text": "(1) $\\{x\\mid 1\\leqslant x <   2\\}$",
                "lines": [
                    {
                        "bbox": [223.12, 111.01, 323.09, 126.99],
                        "spans": [
                            {"t": "text", "c": "(1)"},
                            {"t": "eq", "c": "\\{x\\mid 1\\leqslant x <   2\\}"},
                        ],
                    }
                ],
            }
        ],
    }
    html = _render(page, tmp_path)
    assert "<svg" not in html
    assert "missing.jpg" not in html
    assert 'jax="CHTML"' in html
    assert "mjx-math" in html


def test_title_renders_formula_as_mathjax_placeholder(tmp_path: Path) -> None:
    """标题里的行内公式当场画完，不能把 $...$ 原样印出来，也不能只留空壳。"""
    page = _title_page(
        "焦裕禄$^{①}$",
        [[{"t": "text", "c": "焦裕禄"}, {"t": "eq", "c": "^{①}"}]],
    )
    html = _render(page, tmp_path)
    assert "$^{①}$" not in html
    assert 'data-latex="' not in html
    assert ("mjx-math" in html or "math-error" in html)
    assert "<svg" not in html
    assert "焦裕禄" in html


def test_interline_paints_tex_not_missing_image(tmp_path: Path) -> None:
    """行间公式有字就画字，不因为缺图把公式丢掉。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "eqc",
                "type": "interline_equation",
                "kind": "body",
                "bbox": [167.0, 298.0, 282.0, 313.0],
                "asset": "missing.jpg",
                "text": "$$C = \\{(x, y) | x y < 0 \\}.$$",
                "lines": [
                    {
                        "spans": [
                            {"t": "ieq", "c": "C = \\{(x, y) | x y < 0 \\}."},
                        ]
                    }
                ],
            }
        ],
    }
    html = _render(page, tmp_path)
    assert "missing.jpg" not in html
    assert "<svg" not in html
    assert 'jax="CHTML"' in html or "C =" in html
    assert 'data-latex="' not in html
    assert 'data-kb-ink="eq"' in html
    fs = re.search(r'data-kb-ink="eq"[^>]*--kb-fs:([0-9.]+)', html)
    assert fs and float(fs.group(1)) > 4


def test_interline_font_fits_two_row_system(tmp_path: Path) -> None:
    """行间公式跟例题一样，直接用正文字号，不读 eq-display。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12, "eq-display": 13},
        "blocks": [
            {
                "id": "sys",
                "type": "interline_equation",
                "kind": "body",
                "bbox": [155.0, 497.0, 293.0, 532.0],
                "text": (
                    "$$\\left\\{ \\begin{array}{l l} 2 x = 4, \\\\ x + y = 8 "
                    "\\end{array} \\text {或} \\left\\{ \\begin{array}{l l} "
                    "2 x = 8, \\\\ x + y = 4. \\end{array} \\right. \\right.$$"
                ),
            }
        ],
    }
    html = _render(page, tmp_path)
    fs = re.search(r'data-kb-ink="eq"[^>]*--kb-fs:([0-9.]+)', html)
    assert fs
    assert float(fs.group(1)) == 12
    assert 'display="true"' in html
    assert 'jax="CHTML"' in html


def test_page18_single_line_eq_uses_inline_and_caps_fs(tmp_path: Path) -> None:
    """第 18 页三道矮框：行内体，字号跟正文一样，不按框另算。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12, "eq-display": 13},
        "blocks": [
            {
                "id": "kb-781f420755719b16",
                "type": "interline_equation",
                "kind": "body",
                "bbox": [97.0, 201.0, 350.0, 217.0],
                "text": (
                    "$$\nA \\bigcup B = \\{a, b, c, d \\}, "
                    "\\overline {{A}} = \\{d, e \\}, "
                    "\\overline {{B}} = \\{a, b, e \\}.\n$$"
                ),
                "lines": [{
                    "bbox": [97.0, 201.0, 350.0, 217.0],
                    "spans": [{
                        "t": "ieq",
                        "c": (
                            "A \\bigcup B = \\{a, b, c, d \\}, "
                            "\\overline {{A}} = \\{d, e \\}, "
                            "\\overline {{B}} = \\{a, b, e \\}."
                        ),
                    }],
                }],
            },
            {
                "id": "kb-bb10ea915a9b6632",
                "type": "interline_equation",
                "kind": "body",
                "bbox": [153.0, 238.0, 295.0, 255.0],
                "text": (
                    "$$\n\\overline {{{A \\cup B}}} = \\{e \\}, "
                    "\\overline {{{A}}} \\cap \\overline {{{B}}} = \\{e \\}.\n$$"
                ),
                "lines": [{
                    "bbox": [153.0, 238.0, 295.0, 255.0],
                    "spans": [{
                        "t": "ieq",
                        "c": (
                            "\\overline {{{A \\cup B}}} = \\{e \\}, "
                            "\\overline {{{A}}} \\cap \\overline {{{B}}} = \\{e \\}."
                        ),
                    }],
                }],
            },
            {
                "id": "kb-2f55d07ce569ca29",
                "type": "interline_equation",
                "kind": "body",
                "bbox": [112.0, 277.0, 334.0, 293.0],
                "text": (
                    "$$\n\\overline {{{A \\cap B}}} = \\{a, b, d, e \\}, "
                    "\\overline {{{A}}} \\cup \\overline {{{B}}} = \\{a, b, d, e \\}.\n$$"
                ),
                "lines": [{
                    "bbox": [112.0, 277.0, 334.0, 293.0],
                    "spans": [{
                        "t": "ieq",
                        "c": (
                            "\\overline {{{A \\cap B}}} = \\{a, b, d, e \\}, "
                            "\\overline {{{A}}} \\cup \\overline {{{B}}} = \\{a, b, d, e \\}."
                        ),
                    }],
                }],
            },
        ],
    }
    html = _render(page, tmp_path)
    hem = 1.283
    for block_id, box_h in (
        ("kb-781f420755719b16", 16.0),
        ("kb-bb10ea915a9b6632", 17.0),
        ("kb-2f55d07ce569ca29", 16.0),
    ):
        chunk = re.search(
            rf'<div class="kb-ir-ink"[^>]*data-kb-block="{re.escape(block_id)}"[^>]*>.*?</div>',
            html,
            re.S,
        )
        assert chunk, block_id
        assert 'display="true"' not in chunk.group(0)
        fs = _fs(html, block_id)
        assert fs * hem <= box_h + 1e-6
        assert fs == 12
        assert abs(_ink_var(html, block_id, "--kb-lh") - box_h) < 0.05


def _page_ir_theme_css() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "surfaces"
        / "page-ir"
        / "theme.css"
    ).read_text(encoding="utf-8")


def test_ink_css_kills_display_math_margin() -> None:
    """行间公式框盖得过 MathJax 的 display 上下边距，否则一行高的框里只剩底色。"""
    css = _page_ir_theme_css()
    hit = re.search(
        r'\.kb-ir-ink mjx-container\[jax="CHTML"\]\[display="true"\]\s*\{([^}]*)\}',
        css,
    )
    assert hit
    body = hit.group(1)
    assert "margin: 0" in body
    assert "inline-block" in body


def test_ink_css_inline_math_stays_inline() -> None:
    """行内公式用 inline，不把正文那一行撑高；行间仍是 inline-block。"""
    css = _page_ir_theme_css()
    inline = re.search(
        r'\.kb-ir-ink mjx-container\[jax="CHTML"\]:not\(\[display="true"\]\)\s*\{([^}]*)\}',
        css,
    )
    assert inline
    inline_body = inline.group(1)
    assert "display: inline" in inline_body
    assert "inline-block" not in inline_body
    assert "vertical-align: middle" not in inline_body
    display = re.search(
        r'\.kb-ir-ink mjx-container\[jax="CHTML"\]\[display="true"\]\s*\{([^}]*)\}',
        css,
    )
    assert display
    assert "inline-block" in display.group(1)
    assert "vertical-align: middle" not in display.group(1)
    math_inline = re.search(
        r'\.kb-ir-ink \.math-inline:not\(\.math--display\)\s*\{([^}]*)\}',
        css,
    )
    assert math_inline
    math_body = math_inline.group(1)
    assert "display: inline" in math_body
    assert "inline-block" not in math_body
    assert "vertical-align: middle" not in math_body
    ink = re.search(r'(?m)^\.kb-ir-ink \{([^}]*)\}', css)
    assert ink
    ink_body = ink.group(1)
    assert "line-height: calc(var(--kb-lh" in ink_body
    assert "overflow: visible" in ink_body
    assert "overflow: hidden" not in ink_body
    sheet = re.search(r'(?m)^\.kb-ir-sheet \{([^}]*)\}', css)
    assert sheet
    assert "overflow: hidden" in sheet.group(1)


def test_two_line_body_eq_stays_inline(tmp_path: Path) -> None:
    """两行正文里的行内公式不当行间画。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "overline A",
                "bbox": [80.0, 200.0, 520.0, 235.0],
                "lines": [
                    {
                        "bbox": [80.0, 200.0, 520.0, 218.0],
                        "spans": [
                            {"t": "text", "c": "集合"},
                            {"t": "eq", "c": "\\overline{A}"},
                        ],
                    },
                    {
                        "bbox": [80.0, 218.0, 520.0, 235.0],
                        "spans": [{"t": "text", "c": "的补集"}],
                    },
                ],
            },
        ],
    }
    html = _render(page, tmp_path)
    assert _ink_var(html, "body", "--kb-lh") == 18
    chunk = re.search(
        r'<div class="kb-ir-ink"[^>]*data-kb-block="body"[^>]*>.*?</div>',
        html,
        re.S,
    )
    assert chunk
    assert 'display="true"' not in chunk.group(0)
    # 容器不许带 vertical-align：mjx-math 是行内块，基线就是 TeX 基线，
    # 再按 -depth 拉一次会让整条公式比同行汉字低 d em。
    assert not re.search(
        r'<mjx-container[^>]*style="[^"]*vertical-align:',
        chunk.group(0),
    )
    assert "math--display" not in chunk.group(0)


def test_title_renders_sub_sup_and_tab(tmp_path: Path) -> None:
    """标题里的下标/上标要出真标签，制表位要出 kb-ir-tab。"""
    page = _title_page(
        "H2O\t甲",
        [
            [
                {"t": "text", "c": "H"},
                {"t": "sub", "c": "2"},
                {"t": "text", "c": "O"},
                {"t": "tab"},
                {"t": "sup", "c": "甲"},
            ]
        ],
    )
    html = _render(page, tmp_path)
    assert "<sub>2</sub>" in html
    assert "<sup>甲</sup>" in html
    assert 'class="kb-ir-tab"' in html


def test_numbered_box_title_paints_full_text(tmp_path: Path) -> None:
    """已落盘 kind=box 的「习题3.1」也要印出数字，不能只印栏目名。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "xiti",
                "type": "title",
                "kind": "box",
                "box_kind": "习题",
                "level": 2,
                "text": "习题3.1",
                "bbox": [113.0, 384.0, 177.0, 403.0],
                "lines": [
                    {
                        "bbox": [113.0, 384.0, 177.0, 403.0],
                        "spans": [{"t": "text", "c": "习题3.1"}],
                    }
                ],
            }
        ],
    }
    html = _render(page, tmp_path)
    assert "习题3.1" in html
    assert "kb-ir-box-title" not in html


def test_numbered_box_does_not_swallow_following(tmp_path: Path) -> None:
    """编号习题标题不当栏目框，后面的题目不能被收进侧栏。"""
    page = {
        "blocks": [
            {
                "id": "xiti",
                "type": "title",
                "kind": "box",
                "box_kind": "习题",
                "level": 2,
                "text": "习题3.1",
                "lines": [[{"t": "text", "c": "习题3.1"}]],
            },
            {"id": "q", "type": "text", "kind": "body", "text": "1. 第一题"},
        ],
        "layout": {
            "tree": {
                "id": "page",
                "kind": "rows",
                "children": [{"id": "r0", "kind": "flow", "members": [0, 1]}],
            }
        },
    }
    html = _render(page, tmp_path)
    assert "习题3.1" in html
    assert "1. 第一题" in html
    assert "kb-ir-box" not in html


def test_exact_box_label_still_aside(tmp_path: Path) -> None:
    """整名栏目（可带末尾冒号）仍走栏目框。"""
    page = {
        "blocks": [
            {
                "id": "think",
                "type": "title",
                "kind": "box",
                "box_kind": "思考与讨论",
                "text": "思考与讨论：",
            },
            {"id": "q", "type": "text", "kind": "body", "text": "米曲霉起了什么作用？"},
        ],
        "layout": {
            "tree": {
                "id": "page",
                "kind": "rows",
                "children": [{"id": "r0", "kind": "flow", "members": [0, 1]}],
            }
        },
    }
    html = _render(page, tmp_path)
    assert "kb-ir-box-title" in html
    assert "思考与讨论" in html
    assert "米曲霉起了什么作用？" in html


def test_title_without_lines_falls_back_to_text(tmp_path: Path) -> None:
    """老页面没有 lines 字段时，仍然按 text 渲染，且必须转义。"""
    html = _render(_title_page("纯标题", None), tmp_path)
    assert "纯标题" in html
    assert "<h2" in html


def test_title_text_stays_escaped(tmp_path: Path) -> None:
    """走 text 兜底时不能把标题当 HTML 注进去。"""
    html = _render(_title_page("<img src=x onerror=1>", None), tmp_path)
    assert "&lt;img" in html
    assert "<img src=x" not in html


def test_title_span_text_stays_escaped(tmp_path: Path) -> None:
    """走 lines 时同样要转义，别因为改成 linesHtml 就开了注入口子。"""
    page = _title_page("x", [[{"t": "text", "c": "<script>bad()</script>"}]])
    html = _render(page, tmp_path)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def _ink_var(html: str, block_id: str, key: str) -> float:
    pat = (
        rf'data-kb-ink="[^"]+" data-kb-block="{re.escape(block_id)}"'
        rf'[^>]*{re.escape(key)}:([0-9.]+)'
    )
    m = re.search(pat, html)
    assert m, html[:500]
    return float(m.group(1))


def _fs(html: str, block_id: str) -> float:
    return _ink_var(html, block_id, "--kb-fs")


def test_font_counts_visible_latex_not_source(tmp_path: Path) -> None:
    """公式按渲染后看得见的字数算，不能把美元和命令当字宽。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "plain",
                "type": "text",
                "kind": "body",
                "text": "(3) 1 是自然数吗?",
                "bbox": [89.0, 649.0, 193.0, 664.0],
            },
            {
                "id": "eq4",
                "type": "text",
                "kind": "body",
                "text": "(4) $3 > \\pi$;",
                "bbox": [90.0, 668.0, 147.0, 682.0],
            },
            {
                "id": "eq5",
                "type": "text",
                "kind": "body",
                "text": "(5)$2 \\in (0, 5)$, 且$2 \\in \\mathbf{Z}$.",
                "bbox": [89.0, 687.0, 222.0, 702.0],
            },
        ],
    }
    html = _render(page, tmp_path)
    plain = _fs(html, "plain")
    eq4 = _fs(html, "eq4")
    eq5 = _fs(html, "eq5")
    assert eq5 >= 10.0
    assert eq4 >= 9.5
    assert abs(eq5 - plain) / plain < 0.15
    assert abs(eq4 - plain) / plain < 0.15


def test_sheet_paints_caption_in_child_box(tmp_path: Path) -> None:
    """墨稿按子块框另贴题注，不把 figcaption 塞进图身。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "fig",
                "type": "image",
                "kind": "figure",
                "asset": "cantor.jpg",
                "caption": "康托脚注",
                "bbox": [95.0, 253.0, 155.0, 346.0],
                "captions": [
                    {
                        "type": "image_footnote",
                        "text": "康托脚注",
                        "bbox": [67.0, 349.0, 183.0, 397.0],
                    }
                ],
            }
        ],
    }
    html = _render(page, tmp_path)
    assert "<figcaption>" not in html
    assert 'alt="康托脚注"' in html
    assert 'data-kb-ink="caption-fig-note"' in html
    assert 'data-kb-block="fig-cap0"' in html
    assert "康托脚注" in html
    assert "<figcaption>" not in html
    assert "left:11.2605%" in html
    assert "top:41.4982%" in html


def test_table_font_fits_cells_not_whole_box(tmp_path: Path) -> None:
    """表按格子封顶，不能把整张表当一段正文去套出 25 点。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [55.0, 397.0, 395.0, 484.0],
                "table_html": (
                    "<table><tr><td>数集</td><td>符号</td></tr>"
                    "<tr><td>自然数集</td><td>N</td></tr>"
                    "<tr><td>整数集</td><td>Z</td></tr>"
                    "<tr><td>有理数集</td><td>Q</td></tr>"
                    "<tr><td>实数集</td><td>R</td></tr></table>"
                ),
            }
        ],
    }
    html = _render(page, tmp_path)
    fs = _fs(html, "tab")
    assert 8.0 <= fs <= 15.2
    assert fs * 1.15 * 5 <= 87.0 + 0.2
    assert _ink_var(html, "tab", "--kb-lh") <= 87.0 / 5 + 0.2
    assert 'data-kb-ink="table"' in html
    assert "自然数集" in html


def test_table_keeps_cell_images(tmp_path: Path) -> None:
    """表内插图要画出来，不能把整张表转义成标签字。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [55.0, 126.0, 395.0, 386.0],
                "table_html": (
                    "<table><tr><td>图像</td>"
                    '<td><img src="images/c7513228c9aac464476a744f69b43e49be5463f8ec7de479088689c49f3159ac.jpg"/></td>'
                    '<td><img src="javascript:alert(1)" onerror="1"></td>'
                    "</tr></table>"
                ),
            }
        ],
    }
    html = _render(page, tmp_path)
    assert "&lt;table" not in html
    assert "&lt;img" not in html
    assert "图像" in html
    assert (
        'src="/api/v1/kb/proxy/asset?key='
        "kb%2Fpdfs%2Fbk%2Fassets%2Fmineru%2F"
        "c7513228c9aac464476a744f69b43e49be5463f8ec7de479088689c49f3159ac.jpg"
        '"'
    ) in html
    assert "javascript:" not in html
    assert "onerror" not in html


def test_table_image_row_does_not_inflate_font(tmp_path: Path) -> None:
    """带图的表不能把图行高度摊进字号。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [55.0, 126.0, 395.0, 386.0],
                "table_html": (
                    "<table><tr><td>$y=a^{x}$</td><td>a&gt;1</td><td>0</td></tr>"
                    '<tr><td>图像</td>'
                    '<td><img src="images/c7513228c9aac464476a744f69b43e49be5463f8ec7de479088689c49f3159ac.jpg"/></td>'
                    '<td><img src="images/590448325f98a7e0c6a1506fd6fb11cb30f2df19402030a22d56a248fade5e5a.jpg"/></td>'
                    "</tr>"
                    '<tr><td rowspan="3">图像特征</td>'
                    "<td colspan=\"2\">(1) 图像都在x轴上方,无限趋近于x轴,但永不相交.</td></tr>"
                    "<tr><td colspan=\"2\">(2) 过点(0,1).</td></tr>"
                    "<tr><td>(3) 由左至右图像上升.</td><td>(3) 由左至右图像下降.</td></tr>"
                    '<tr><td rowspan="3">函数性质</td>'
                    "<td colspan=\"2\">(1) 定义域为R,函数值恒正.</td></tr>"
                    "<tr><td colspan=\"2\">(2) 当x=0时,y=1.</td></tr>"
                    "<tr><td>(3) 在R上是严格增函数.</td><td>(3) 在R上是严格减函数.</td></tr></table>"
                ),
            }
        ],
    }
    html = _render(page, tmp_path)
    assert 8 <= _fs(html, "tab") <= 12
    assert 'class="kb-ir-has-img"' in html
    assert 'class="kb-ir-img-row"' in html
    assert "<colgroup>" in html
    cols = [float(x) for x in re.findall(r'<col style="width:([0-9.]+)%"', html)]
    assert len(cols) == 3
    assert 14 <= cols[0] <= 28
    assert cols[1] > cols[0]
    assert cols[2] > cols[0]
    assert "由左至右图像上升" in html


def test_table_long_cell_does_not_shrink_short_cells(tmp_path: Path) -> None:
    """长格子可以折行，不能为了它一行而把整张表缩到比短格子更小。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [55.0, 100.0, 395.0, 343.0],
                "table_html": (
                    "<table><tr><td>$y=\\log_a x$</td><td>a&gt;1</td><td>0</td></tr>"
                    "<tr><td>图像</td>"
                    '<td><img src="images/a.jpg"/></td>'
                    '<td><img src="images/b.jpg"/></td></tr>'
                    '<tr><td rowspan="3">图像特征</td>'
                    "<td colspan=\"2\">(1)图像都在y轴右侧,无限趋近于y轴,但永不相交.</td></tr>"
                    "<tr><td colspan=\"2\">(2)过点(1,0).</td></tr>"
                    "<tr><td>(3)由左至右图像上升.</td><td>(3)由左至右图像下降.</td></tr>"
                    '<tr><td rowspan="3">函数性质</td>'
                    "<td colspan=\"2\">(1)定义域为(0,+∞).</td></tr>"
                    "<tr><td colspan=\"2\">(2)当x=1时,y=0.</td></tr>"
                    "<tr><td>(3)在区间(0,+∞)上是严格增函数.</td>"
                    "<td>(3)在区间(0,+∞)上是严格减函数.</td></tr></table>"
                ),
            }
        ],
    }
    html = _render(page, tmp_path)
    assert 10 <= _fs(html, "tab") <= 12
    cols = [float(x) for x in re.findall(r'<col style="width:([0-9.]+)%"', html)]
    assert len(cols) == 3
    assert cols[1] > cols[0]


def test_table_math_fits_column_and_box(tmp_path: Path) -> None:
    """长公式必须缩进本列、整表框里，不能画进下一列、也不能高出外框。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [55.0, 290.0, 395.0, 483.0],
                "table_html": (
                    "<table>"
                    "<tr><td>$f(x)=ax^{2}+bx+c$</td>"
                    "<td>$b^{2}-4ac>0$</td>"
                    "<td>$b^{2}-4ac=0$</td>"
                    "<td>$b^{2}-4ac<0$</td></tr>"
                    "<tr><td>零点</td><td>$x_{1}、x_{2}$</td><td>$x_{0}$</td><td>不存在</td></tr>"
                    '<tr><td>大致图像</td>'
                    '<td><img src="images/a.jpg"/></td>'
                    '<td><img src="images/b.jpg"/></td>'
                    '<td><img src="images/c.jpg"/></td></tr>'
                    "<tr><td>$f(x)>0$ 的解集</td>"
                    "<td>$(-∞,x_{1})∪(x_{2},+∞)$</td>"
                    "<td>$\\{x|x≠x_{0}\\}$</td><td>R</td></tr>"
                    "<tr><td>$f(x)<0$ 的解集</td>"
                    "<td>$(x_{1},x_{2})$</td><td>∅</td><td>∅</td></tr>"
                    "</table>"
                ),
            }
        ],
    }
    html = _render(page, tmp_path)
    fs = _fs(html, "tab")
    lh = _ink_var(html, "tab", "--kb-lh")
    assert 4 <= fs <= 10
    assert lh <= fs * 1.2 + 0.05


def test_table_font_long_cell_uses_cell_width(tmp_path: Path) -> None:
    """长格子按列宽和行高封顶，不能按整表宽折行。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [55.0, 600.0, 394.0, 721.0],
                "table_html": (
                    "<table>"
                    "<tr><td>陈述句 α</td><td>α 的否定形式</td></tr>"
                    "<tr><td>x&gt;1</td><td>x≤1</td></tr>"
                    "<tr><td>x&gt;1 或 y&gt;1</td><td>x≤1 且 y≤1</td></tr>"
                    "<tr><td>集合 A 中满足性质 p 的元素至少有两个</td>"
                    "<td>集合 A 中满足性质 p 的元素最多有一个</td></tr>"
                    "<tr><td>所有的 a∈A 满足性质 p</td><td>至少存在一个 a∈A 不满足性质 p</td></tr>"
                    "<tr><td>所有的 a∈A 不满足性质 p</td><td>至少存在一个 a∈A 满足性质 p</td></tr>"
                    "</table>"
                ),
            }
        ],
    }
    html = _render(page, tmp_path)
    fs = _fs(html, "tab")
    assert fs == int(fs)
    assert 8 <= fs <= 12
    assert _ink_var(html, "tab", "--kb-lh") <= 121.0 / 6 + 0.2


def test_table_two_line_font_not_drowned_by_one_line_tables(tmp_path: Path) -> None:
    """两行格子的表用自己摊出来的字号，不被全书一行表的票盖回偏大。"""
    one_line = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "short",
                "type": "table",
                "kind": "body",
                "bbox": [55.0, 397.0, 395.0, 484.0],
                "table_html": (
                    "<table><tr><td>数集</td><td>符号</td></tr>"
                    "<tr><td>自然数集</td><td>N</td></tr>"
                    "<tr><td>整数集</td><td>Z</td></tr>"
                    "<tr><td>有理数集</td><td>Q</td></tr>"
                    "<tr><td>实数集</td><td>R</td></tr></table>"
                ),
            }
        ],
    }
    two_line = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "long",
                "type": "table",
                "kind": "body",
                "bbox": [55.0, 600.0, 394.0, 721.0],
                "table_html": (
                    "<table>"
                    "<tr><td>陈述句 α</td><td>α 的否定形式</td></tr>"
                    "<tr><td>x&gt;1</td><td>x≤1</td></tr>"
                    "<tr><td>x&gt;1 或 y&gt;1</td><td>x≤1 且 y≤1</td></tr>"
                    "<tr><td>集合 A 中满足性质 p 的元素至少有两个</td>"
                    "<td>集合 A 中满足性质 p 的元素最多有一个</td></tr>"
                    "<tr><td>所有的 a∈A 满足性质 p</td>"
                    "<td>至少存在一个 a∈A 不满足性质 p</td></tr>"
                    "<tr><td>所有的 a∈A 不满足性质 p</td>"
                    "<td>至少存在一个 a∈A 满足性质 p</td></tr>"
                    "</table>"
                ),
            }
        ],
    }
    html = _render({**two_line, "book_pages": [one_line, two_line]}, tmp_path)
    long_fs = _fs(html, "long")
    assert 8 <= long_fs <= 12
    short_html = _render({**one_line, "book_pages": [one_line, two_line]}, tmp_path)
    short_fs = _fs(short_html, "short")
    assert short_fs >= long_fs


def test_table_paints_cell_formulas(tmp_path: Path) -> None:
    """表格子里的美元公式当场画完，不能原样印 $ax^2$。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [198.0, 554.0, 537.0, 667.0],
                "table_html": (
                    "<table><tr><td colspan=\"2\">a&gt;0</td></tr>"
                    "<tr><td> $ax^{2}+bx+c>0$ </td>"
                    "<td>解集为 $(-∞,x_{1})$ </td></tr>"
                    "<tr><td> $ax^{2}+bx+c<0$ </td>"
                    "<td>解集为 $(x_{1},x_{2})$ </td></tr></table>"
                ),
            }
        ],
    }
    html = _render(page, tmp_path)
    assert "$ax^{2}+bx+c>0$" not in html
    assert "$ax^{2}+bx+c<0$" not in html
    assert "$(-∞,x_{1})$" not in html
    assert html.count('jax="CHTML"') >= 4
    assert "解集为" in html
    assert "a&gt;0" in html or "a>0" in html
    assert 'data-kb-ink="table"' in html


def test_title_ink_css_vertically_centers() -> None:
    """标题墨框字在虚线框里上下空隙一样；不要左右居中。"""
    css = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "surfaces"
        / "page-ir"
        / "theme.css"
    ).read_text(encoding="utf-8")
    for cls in ("title-1", "title-2"):
        rules = re.findall(
            rf'([^{{}}]*\[data-kb-ink="{re.escape(cls)}"\][^{{}}]*)\{{([^}}]*)\}}',
            css,
        )
        assert rules, cls
        joined = " ".join(body for _, body in rules)
        assert "display: flex" in joined or "display:flex" in joined
        assert "align-items: center" in joined or "align-items:center" in joined
        assert "justify-content:center" not in joined.replace(" ", "")


def test_ink_tint_css_skips_body() -> None:
    """底色给标题、图、表、公式、页眉页脚；正文选择器里不能出现 background。"""
    css = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "surfaces"
        / "page-ir"
        / "theme.css"
    ).read_text(encoding="utf-8")
    for cls in (
        "title-1", "title-2", "figure", "eq", "table", "caption",
        "caption-fig", "caption-fig-note", "caption-table",
        "example", "box", "aside", "header", "footer",
    ):
        hit = re.search(
            rf'\[data-kb-ink="{re.escape(cls)}"\](?:\s*,\s*[^{{]+)?\s*\{{([^}}]*background:[^}}]*)\}}',
            css,
        )
        assert hit, cls
    body_rules = [
        part.split("}", 1)[0]
        for part in css.split('[data-kb-ink="body"]')[1:]
    ]
    assert all("background:" not in rule for rule in body_rules)


def test_same_type_shares_one_integer(tmp_path: Path) -> None:
    """同一类型全书一个整数，松框紧框不再各算各的。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "fat",
                "type": "text",
                "kind": "body",
                "text": "除了用自然语言描述集合，我们还有一些其他方法用来表示集合.",
                "bbox": [196.0, 270.0, 540.0, 306.0],
                "lines": [{
                    "bbox": [197.54, 274.17, 536.1, 306.96],
                    "spans": [{"t": "text", "c": "除了用自然语言描述集合，我们还有一些其他方法用来表示集合."}],
                }],
            },
            {
                "id": "tight",
                "type": "text",
                "kind": "body",
                "text": "将集合中的元素不重复地一一列举出来并写在大括号内，这种表示集合的方法叫做列举法。",
                "bbox": [196.0, 309.0, 541.0, 383.0],
                "lines": [
                    {
                        "bbox": [224.31, 312.01, 537.28, 322.1],
                        "spans": [{"t": "text", "c": "将集合中的元素不重复地一一列举出来并写在大括号内，这种表示集合的方法叫做列举法。"}],
                    },
                    {"bbox": [200.52, 329.67, 537.88, 342.29], "spans": []},
                    {"bbox": [199.92, 349.86, 536.69, 360.79], "spans": []},
                    {"bbox": [200.52, 370.04, 395.08, 380.13], "spans": []},
                ],
            },
        ],
    }
    html = _render(page, tmp_path)
    fat = _fs(html, "fat")
    tight = _fs(html, "tight")
    assert fat == tight
    assert fat == int(fat)


def test_stored_fonts_table_wins(tmp_path: Path) -> None:
    """页上挂了全书字号表就从这张表起算。标题再按框高和正文字号收一档。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 11, "title-2": 18},
        "blocks": [
            {
                "id": "title",
                "type": "title",
                "level": 2,
                "kind": "body",
                "text": "2 集合的表示方法",
                "bbox": [207.0, 220.0, 373.0, 242.0],
            },
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "除了用自然语言描述集合.",
                "bbox": [196.0, 270.0, 540.0, 306.0],
            },
        ],
    }
    html = _render(page, tmp_path)
    assert _fs(html, "title") == 18
    assert _fs(html, "body") == 11


def test_title_paint_caps_to_box(tmp_path: Path) -> None:
    """同一级标题共用字号表里的那一个数，不按每一只框再改。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"title-1": 35},
        "blocks": [
            {
                "id": "title",
                "type": "title",
                "level": 1,
                "kind": "body",
                "text": "1.1 集合初步",
                "bbox": [80.0, 100.0, 400.0, 136.0],
            },
        ],
    }
    html = _render(page, tmp_path)
    assert _fs(html, "title") == 35


def test_same_type_on_a_page_shares_one_size(tmp_path: Path) -> None:
    """同一页里，同一类字号相同。窄框不再单独缩小。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12, "title-2": 15, "aside": 14},
        "leads": {"body": 15, "title-2": 15, "aside": 16},
        "blocks": [
            {
                "id": "wide",
                "type": "text",
                "kind": "body",
                "slot": "main",
                "text": "Have you ever wondered what makes people happy?",
                "bbox": [180.0, 200.0, 520.0, 280.0],
            },
            {
                "id": "narrow",
                "type": "text",
                "kind": "body",
                "slot": "main",
                "text": "money",
                "bbox": [40.0, 200.0, 90.0, 214.0],
            },
            {
                "id": "head-a",
                "type": "title",
                "level": 2,
                "kind": "body",
                "text": "Pre-reading activity",
                "bbox": [40.0, 80.0, 180.0, 100.0],
            },
            {
                "id": "head-b",
                "type": "title",
                "level": 2,
                "kind": "body",
                "text": "1 Before you read, choose one of the things that you think makes people happy.",
                "bbox": [40.0, 110.0, 160.0, 150.0],
            },
        ],
    }
    html = _render(page, tmp_path)
    assert _fs(html, "wide") == _fs(html, "narrow") == 12
    assert _fs(html, "head-a") == _fs(html, "head-b") == 15
    css = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / "surfaces"
        / "page-ir"
        / "theme.css"
    ).read_text(encoding="utf-8")
    assert "Noto Sans SC" in css
    assert css.index("Noto Sans SC") < css.index("Microsoft YaHei")
    assert "SimSun" not in css


def test_exercise_title_paints_as_title_3(tmp_path: Path) -> None:
    """练习/习题不当二级标题，走更小的三级标题字号。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12, "title-2": 15, "title-3": 10},
        "blocks": [
            {
                "id": "drill",
                "type": "title",
                "level": 2,
                "kind": "body",
                "text": "练习1.1(1)",
                "bbox": [80.0, 700.0, 160.0, 717.0],
            },
        ],
    }
    html = _render(page, tmp_path)
    assert 'data-kb-ink="title-3"' in html
    assert _fs(html, "drill") == 10


def test_stored_leads_body_lh(tmp_path: Path) -> None:
    """页上挂了全书行距表，正文用表里的行距，不再用字号乘 1.087。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "我们经常需要把一些对象放在一起研究.",
                "bbox": [80.0, 200.0, 520.0, 235.0],
            },
        ],
    }
    html = _render(page, tmp_path)
    assert _fs(html, "body") == 12
    assert _ink_var(html, "body", "--kb-lh") == 18


def test_printed_lines_do_not_shrink_or_wrap(tmp_path: Path) -> None:
    """字放进各自的框，由框宽折行。字号不缩小。只有多折出一行时才并到上一行行尾。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12, "title-1": 35, "header": 9},
        "leads": {"body": 18, "title-1": 40, "header": 12},
        "printed_lines": {"credit": 1, "para": 2, "title": 1, "notice": 2, "chrome:headers:0": 1},
        "blocks": [
            {
                "id": "credit",
                "type": "text",
                "kind": "body",
                "text": "出版 上海教育出版社有限公司（上海市永福路123号）",
                "bbox": [80.0, 200.0, 360.0, 214.0],
                "lines": [[{"t": "text", "c": "出版 上海教育出版社有限公司（上海市永福路123号）"}]],
            },
            {
                "id": "para",
                "type": "text",
                "kind": "body",
                "text": "第一行很长很长。第二行也很长。",
                "bbox": [80.0, 230.0, 400.0, 268.0],
                "lines": [
                    [{"t": "text", "c": "第一行很长很长。"}],
                    [{"t": "text", "c": "第二行也很长。"}],
                ],
            },
            {
                "id": "title",
                "type": "title",
                "level": 1,
                "kind": "body",
                "text": "1.1 集合初步",
                "bbox": [80.0, 100.0, 400.0, 136.0],
                "lines": [[{"t": "text", "c": "1.1 集合初步"}]],
            },
            {
                "id": "notice",
                "type": "text",
                "kind": "body",
                "text": "声明一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯",
                "bbox": [66.0, 639.0, 511.0, 672.0],
                "lines": [
                    [{"t": "text", "c": "声明一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯"}],
                    [],
                ],
            },
        ],
        "chrome": {
            "headers": [{"text": "普通高中教科书 数学", "bbox": [80.0, 20.0, 200.0, 34.0]}],
            "footers": [],
            "page_numbers": [],
            "footnotes": [],
        },
    }
    html = _render(page, tmp_path)
    assert "--kb-fit:" not in html
    assert _fs(html, "credit") == 12
    assert _ink_var(html, "credit", "--kb-lh") == 18
    para = re.search(r'data-kb-block="para"[^>]*>.*?</div>', html, re.S)
    assert para
    assert "第一行很长很长。" in para.group(0)
    assert "第二行也很长。" in para.group(0)
    notice = re.search(r'data-kb-block="notice"[^>]*>.*?</div>', html, re.S)
    assert notice
    assert "声明一二三四五六七八九十" in notice.group(0)
    assert _fs(html, "title") == 35
    assert 'data-kb-ink="header"' in html
    assert "--kb-fs:9" in html


def test_one_extra_line_folds_into_previous(tmp_path: Path) -> None:
    """多一行但框高只够一行：接到上一行行尾，不许再折下来。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "printed_lines": {"body": 1},
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "可以证明，当分数相等。",
                "bbox": [80.0, 200.0, 160.0, 218.0],
                "lines": [
                    [{"t": "text", "c": "可以证明，当分数相"}],
                    [{"t": "text", "c": "等。"}],
                ],
            },
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-block="body" data-kb-lines="1"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert chunk.group(0).count('class="kb-ir-line"') == 1
    assert "可以证明，当分数相" in chunk.group(0)
    assert "等。" in chunk.group(0)


def test_one_extra_line_wraps_when_box_has_room(tmp_path: Path) -> None:
    """多一行但框高还能再放下这一行：按换行画，不接到行尾。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "printed_lines": {"body": 1},
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "可以证明，当分数相等。一二三四五六七八",
                "bbox": [80.0, 200.0, 280.0, 250.0],
                "lines": [[{"t": "text", "c": "可以证明，当分数相等。一二三四五六七八"}]],
            },
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-ink="body"[^>]*data-kb-block="body"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert "data-kb-lines" not in chunk.group(0)
    assert chunk.group(0).count('class="kb-ir-line"') == 0
    assert "可以证明，当分数相等。" in chunk.group(0)
    assert "一二三四五六七八" in chunk.group(0)


def test_first_indent_does_not_fake_extra_line(tmp_path: Path) -> None:
    """首行窄、后行满宽。21 个汉字：若每行都减缩进会算出 3 行，正确算法是 2 行，不并行。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "printed_lines": {"body": 2},
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "一二三四五六七八九十甲乙丙丁戊己庚辛壬",
                "bbox": [80.0, 200.0, 230.0, 270.0],
                "first_indent": 24,
                "lines": [[{"t": "text", "c": "一二三四五六七八九十甲乙丙丁戊己庚辛壬"}]],
            },
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-ink="body"[^>]*data-kb-block="body"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert "data-kb-lines" not in chunk.group(0)
    assert chunk.group(0).count('class="kb-ir-line"') == 0
    assert "--kb-ind:24" in chunk.group(0)
    assert "一二三四五六七八九十甲乙丙丁戊己庚辛壬" in chunk.group(0)


def test_title_extra_line_folds_when_box_is_short(tmp_path: Path) -> None:
    """标题也按同一条：框高只够一行，多出来的接到行尾。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"title-2": 18},
        "leads": {"title-2": 20},
        "printed_lines": {"head": 1},
        "blocks": [
            {
                "id": "head",
                "type": "title",
                "level": 2,
                "kind": "body",
                "text": "幂函数、指数函数与对数函数",
                "bbox": [80.0, 100.0, 280.0, 120.0],
                "lines": [[{"t": "text", "c": "幂函数、指数函数与对数函数"}]],
            },
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-ink="title-2"[^>]*data-kb-block="head"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert 'data-kb-lines="1"' in chunk.group(0)
    assert chunk.group(0).count('class="kb-ir-line"') == 1
    assert "幂函数" in chunk.group(0)
    assert "对数函数" in chunk.group(0)


def test_caption_folds_when_printed_missing_and_box_is_short(tmp_path: Path) -> None:
    """矮框题注不可能两行：按一行画，不按折行摊高。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"caption-fig-note": 12},
        "leads": {"caption-fig-note": 18},
        "blocks": [
            {
                "id": "fig",
                "type": "image",
                "kind": "figure",
                "asset": "cantor.jpg",
                "bbox": [80.0, 200.0, 200.0, 320.0],
                "captions": [
                    {
                        "type": "image_footnote",
                        "text": "康托是德国数学家集合论创始人",
                        "bbox": [80.0, 330.0, 200.0, 348.0],
                    }
                ],
            }
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-block="fig-cap0"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert 'data-kb-lines="1"' in chunk.group(0)
    assert chunk.group(0).count('class="kb-ir-line"') == 1
    assert "康托是德国数学家" in chunk.group(0)
    assert 11 <= _fs(html, "fig-cap0") <= 14


def test_table_caption_wraps_when_box_has_room(tmp_path: Path) -> None:
    """表注多一行但框里还放得下：按换行画。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"caption-table": 12},
        "leads": {"caption-table": 18},
        "printed_lines": {"tab-cap0": 1},
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [80.0, 200.0, 400.0, 360.0],
                "table_html": "<table><tr><td>数集</td></tr></table>",
                "captions": [
                    {
                        "type": "table_caption",
                        "text": "表5-1常用数集的符号一二三四五六七八九十",
                        "bbox": [80.0, 180.0, 280.0, 230.0],
                    }
                ],
            }
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-block="tab-cap0"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert "表5-1常用数集的符号" in chunk.group(0)


def test_example_extra_line_folds_when_box_is_short(tmp_path: Path) -> None:
    """例题跟正文同一条：多一行且框里放不下，接到行尾。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "printed_lines": {"ex": 1},
        "blocks": [
            {
                "id": "ex",
                "type": "text",
                "kind": "body",
                "text": "例3 用列举法表示下列集合一二三四",
                "bbox": [80.0, 200.0, 200.0, 218.0],
                "lines": [[{"t": "text", "c": "例3 用列举法表示下列集合一二三四"}]],
            },
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-ink="example"[^>]*data-kb-block="ex"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert 'data-kb-lines="1"' in chunk.group(0)
    assert "例3 用列举法" in chunk.group(0)


def test_one_line_box_stays_one_line_when_ruler_says_fit(tmp_path: Path) -> None:
    """印刷 1 行、框高不够 2 行：即使尺子报「刚好 1 行」，也不许样式表再折一行。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "printed_lines": {"fill": 1, "trans": 1, "join": 1, "setb": 1},
        "blocks": [
            {
                "id": "fill",
                "type": "text",
                "kind": "body",
                "text": "2. 用符号“⊃”“=”或“⊂”填空：",
                "bbox": [88.0, 272.0, 257.0, 287.0],
            },
            {
                "id": "trans",
                "type": "text",
                "kind": "body",
                "text": "（3）传递性：若$A \\subseteq B$且$B \\subseteq C$，则$A \\subseteq C$.",
                "bbox": [78.0, 257.0, 309.0, 272.0],
                "lines": [[{"t": "text", "c": "（3）传递性：若"}, {"t": "eq", "c": "A \\subseteq B"}, {"t": "text", "c": "且"}, {"t": "eq", "c": "B \\subseteq C"}, {"t": "text", "c": "，则"}, {"t": "eq", "c": "A \\subseteq C"}, {"t": "text", "c": "."}]],
            },
            {
                "id": "join",
                "type": "text",
                "kind": "body",
                "text": "4. 用符号“⊂”“=”或“⊃”连接集合 A 与 B:",
                "bbox": [88.0, 140.0, 321.0, 154.0],
            },
            {
                "id": "setb",
                "type": "text",
                "kind": "body",
                "text": "(1) $A = \\{x\\mid x^2 -2x + 1 = 0\\}$， $B = \\{x\\mid x^2 -1 = 0\\}$",
                "bbox": [89.0, 158.0, 355.0, 173.0],
            },
        ],
    }
    html = _render(page, tmp_path)
    for bid, needle in (
        ("fill", "填空"),
        ("trans", "传递性"),
        ("join", "连接集合"),
        ("setb", "x^2 -1"),
    ):
        chunk = re.search(rf'data-kb-block="{bid}"[^>]*>', html)
        assert chunk, bid
        assert 'data-kb-lines="1"' in chunk.group(0), bid
        assert needle in html


def test_font_samples_vote_on_paint(tmp_path: Path) -> None:
    """全书中间页的行高在画页取众数；例题跟正文同一个号。"""
    line = {"t": "text", "c": "集合", "bbox": [80.0, 200.0, 200.0, 213.04]}
    page = {
        "page_size": [595, 841],
        "book_pages": [
            {
                "blocks": [
                    {
                        "id": "a",
                        "type": "text",
                        "kind": "body",
                        "text": "集合",
                        "bbox": [80.0, 200.0, 200.0, 213.04],
                        "lines": [[line]],
                    }
                ]
            }
        ],
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "集合",
                "bbox": [80.0, 200.0, 200.0, 218.0],
            },
            {
                "id": "ex",
                "type": "text",
                "kind": "body",
                "text": "例3 用列举法",
                "bbox": [80.0, 240.0, 200.0, 258.0],
            },
        ],
    }
    html = _render(page, tmp_path)
    body = re.search(r'data-kb-ink="body"[^>]*data-kb-block="body"[^>]*>', html)
    ex = re.search(r'data-kb-ink="example"[^>]*data-kb-block="ex"[^>]*>', html)
    assert body and "--kb-fs:12" in body.group(0)
    assert ex and "--kb-fs:12" in ex.group(0)


def test_header_extra_line_folds_when_box_is_short(tmp_path: Path) -> None:
    """页眉跟正文同一条：多一行且框里放不下，接到行尾。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"header": 10},
        "leads": {"header": 12},
        "printed_lines": {"chrome:headers:0": 1},
        "blocks": [
            {
                "id": "keep",
                "type": "text",
                "kind": "body",
                "text": "正文",
                "bbox": [80.0, 200.0, 200.0, 218.0],
            }
        ],
        "chrome": {
            "headers": [{
                "text": "普通高中教科书数学必修第一册页眉文字",
                "bbox": [80.0, 20.0, 200.0, 32.0],
            }],
            "footers": [],
            "page_numbers": [],
            "footnotes": [],
        },
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-block="chrome:headers:0"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert 'data-kb-lines="1"' in chunk.group(0)
    assert "普通高中教科书" in chunk.group(0)


def test_footer_extra_line_folds_when_box_is_short(tmp_path: Path) -> None:
    """页脚跟正文同一条：多一行且框里放不下，接到行尾。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"footer": 10},
        "leads": {"footer": 12},
        "printed_lines": {"chrome:footers:0": 1},
        "blocks": [
            {
                "id": "keep",
                "type": "text",
                "kind": "body",
                "text": "正文",
                "bbox": [80.0, 200.0, 200.0, 218.0],
            }
        ],
        "chrome": {
            "headers": [],
            "footers": [{
                "text": "上海教育出版社普通高中教科书页脚",
                "bbox": [80.0, 800.0, 200.0, 812.0],
            }],
            "page_numbers": [],
            "footnotes": [],
        },
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-block="chrome:footers:0"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert 'data-kb-lines="1"' in chunk.group(0)
    assert "上海教育出版社" in chunk.group(0)


def test_two_extra_lines_do_not_fold(tmp_path: Path) -> None:
    """只有多一行才并；多两行就原样放进框，不再拆也不再并。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "printed_lines": {"body": 1},
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "第一行字。第二行字。第三行字。",
                "bbox": [80.0, 200.0, 160.0, 260.0],
                "lines": [
                    [{"t": "text", "c": "第一行字。"}],
                    [{"t": "text", "c": "第二行字。"}],
                    [{"t": "text", "c": "第三行字。"}],
                ],
            },
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-block="body"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert chunk.group(0).count('class="kb-ir-line"') == 0
    assert "第一行字。" in chunk.group(0)
    assert "第三行字。" in chunk.group(0)


_FN_DRIVER = """
const fs = require("fs");
(async () => {
  const mod = await import(process.env.LIB);
  const fn = mod[process.env.FN];
  const args = JSON.parse(fs.readFileSync(process.env.ARGS, "utf8"));
  process.stdout.write(JSON.stringify(fn(...args)));
})();
"""


def _call_fn(name: str, args: list, tmp_path: Path):
    node = shutil.which("node")
    if not node:
        pytest.skip("这台机器没有 node")
    args_file = tmp_path / "args.json"
    args_file.write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        [node, "-e", _FN_DRIVER],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin",
            "LIB": _LIB.as_uri(),
            "FN": name,
            "ARGS": str(args_file),
        },
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_ocr_printed_lines_computed_in_js(tmp_path: Path) -> None:
    """印刷行数来自中间文件的 lines，不读原始 model.json。"""
    page = {
        "page": 1,
        "page_size": [100, 100],
        "blocks": [
            {
                "id": "fill",
                "type": "text",
                "kind": "body",
                "text": "用符号填空",
                "bbox": [10.0, 10.0, 90.0, 22.0],
                "lines": [[{"t": "text", "c": "用符号填空"}]],
            }
        ],
    }
    printed = _call_fn("printedLinesForPage", [page], tmp_path)
    assert printed["fill"] == 1


def test_printed_lines_count_empty_optical_rows(tmp_path: Path) -> None:
    """印刷行数看框里有几个行框。后几行可以没字，不能只数有字的行。"""
    page = {
        "page": 14,
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "venn",
                "type": "text",
                "kind": "body",
                "text": "我们常用文氏图来直观表示集合.",
                "bbox": [52.0, 84.0, 396.0, 118.0],
                "lines": [
                    {
                        "bbox": [80.33, 85.78, 394.49, 97.56],
                        "spans": [{"t": "text", "c": "我们常用文氏图来直观表示集合."}],
                    },
                    {
                        "bbox": [57.72, 104.28, 262.39, 116.9],
                        "spans": [],
                    },
                ],
            }
        ],
    }
    printed = _call_fn("printedLinesForPage", [page], tmp_path)
    assert printed["venn"] == 2


def test_two_line_box_wraps_when_lead_is_slightly_over(tmp_path: Path) -> None:
    """框高 33、行距 18，两行只差 3 点，四舍五入仍是两行，应该折下来，不能锁成一行。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12},
        "leads": {"body": 18},
        "blocks": [
            {
                "id": "proof",
                "type": "text",
                "kind": "body",
                "text": "6. 证明：“四边形 ABCD 是平行四边形”是“四边形 ABCD 的对角线互相平分”的充要条件.",
                "bbox": [66.0, 500.0, 541.0, 533.0],
                "lines": [{
                    "bbox": [66.0, 500.0, 541.0, 532.8],
                    "spans": [{"t": "text", "c": "6. 证明：“四边形 ABCD 是平行四边形”是“四边形 ABCD 的对角线互相平分”的充要条件."}],
                }],
            }
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'<div class="kb-ir-ink"[^>]*data-kb-block="proof"[^>]*>', html)
    assert chunk
    assert "data-kb-lines=" not in chunk.group(0)


def test_vote_leads_from_printed_line_boxes(tmp_path: Path) -> None:
    """框里行距用框高除以印刷行数。后几行可以没字，不能用字号乘 1.087 顶替。"""
    page = {
        "page": 14,
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "我们常用文氏图来直观表示集合.",
                "bbox": [52.0, 84.0, 396.0, 118.0],
                "lines": [
                    {
                        "bbox": [80.33, 85.78, 394.49, 97.56],
                        "spans": [{"t": "text", "c": "我们常用文氏图来直观表示集合."}],
                    },
                    {
                        "bbox": [57.72, 104.28, 262.39, 116.9],
                        "spans": [],
                    },
                ],
            }
        ],
    }
    leads = _call_fn("voteLeads", [[page]], tmp_path)
    assert leads["body"] == 17
    html = _render(page, tmp_path)
    assert _ink_var(html, "body", "--kb-lh") == 17
    assert _fs(html, "body") == 12


def test_aside_font_uses_slot_rows_not_whole_box(tmp_path: Path) -> None:
    """侧栏只有一行胖框时，按框里折得开的行数摊行高，不能用整栏高度或退回正文 12。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "aside",
                "type": "text",
                "kind": "body",
                "text": "结合例题，试比较分别用自然语言、列举法、描述法和区间表示集合时，其各自的特点和适用对象。",
                "bbox": [52.0, 200.0, 144.0, 259.0],
                "line_height": 53.82,
                "lines": [{
                    "bbox": [54.0, 202.0, 142.0, 255.82],
                    "spans": [{"t": "text", "c": "结合例题，试比较分别用自然语言、列举法、描述法和区间表示集合时，其各自的特点和适用对象。"}],
                }],
            }
        ],
    }
    fonts = _call_fn("voteFonts", [[page]], tmp_path)
    assert fonts["aside"] <= 11
    html = _render(page, tmp_path)
    assert 'data-kb-ink="aside"' in html
    assert _fs(html, "aside") <= 11
    assert _fs(html, "aside") >= 7


def test_figure_sentence_is_body_not_caption(tmp_path: Path) -> None:
    """以「图1-1-4」开头的整句是正文，不能当成题注去拉高题注字号。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"body": 12, "caption-fig": 9},
        "blocks": [
            {
                "id": "explain",
                "type": "text",
                "kind": "body",
                "text": "图 1-1-4(1) 表示集合 A 与 B 既有公共元素又都有非公共元素的情况，",
                "bbox": [80.0, 200.0, 426.0, 291.0],
                "lines": [
                    {
                        "bbox": [80.0, 200.0, 426.0, 212.0],
                        "spans": [{"t": "text", "c": "图 1-1-4(1) 表示集合 A 与 B 既有公共元素又都有非公共元素的情况，"}],
                    }
                ],
            }
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'<div class="kb-ir-ink"[^>]*data-kb-block="explain"[^>]*>', html)
    assert chunk
    assert 'data-kb-ink="body"' in chunk.group(0)
    assert 'data-kb-ink="caption-fig"' not in chunk.group(0)


def test_short_table_and_fig_labels_are_one_line(tmp_path: Path) -> None:
    """书上「表1-1」「图1-1-1」这种矮框，按一行算字号，留白三成。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "tab",
                "type": "table",
                "kind": "body",
                "bbox": [80.0, 200.0, 400.0, 360.0],
                "table_html": "<table><tr><td>数集</td></tr></table>",
                "captions": [
                    {
                        "type": "table_caption",
                        "text": "表1-1 常用数集及其表示方法",
                        "bbox": [167.0, 380.0, 283.0, 395.0],
                    }
                ],
            },
            {
                "id": "fig",
                "type": "image",
                "kind": "figure",
                "bbox": [80.0, 400.0, 200.0, 520.0],
                "captions": [
                    {
                        "type": "image_caption",
                        "text": "图1-1-1",
                        "bbox": [199.0, 531.0, 241.0, 544.0],
                    }
                ],
            },
        ],
    }
    html = _render(page, tmp_path)
    tab = re.search(r'data-kb-block="tab-cap0"[^>]*>.*?</div>', html, re.S)
    fig = re.search(r'data-kb-block="fig-cap0"[^>]*>.*?</div>', html, re.S)
    assert tab and 'data-kb-lines="1"' in tab.group(0)
    assert fig and 'data-kb-lines="1"' in fig.group(0)
    assert _fs(html, "tab-cap0") == 11
    assert _fs(html, "fig-cap0") == 9


def test_short_fig_label_is_caption(tmp_path: Path) -> None:
    """短标签「图1-1-3」仍是题注。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "fig",
                "type": "image",
                "kind": "figure",
                "bbox": [423.0, 104.0, 514.0, 194.0],
                "captions": [{
                    "type": "image_caption",
                    "text": "图1-1-3",
                    "bbox": [447.0, 209.0, 490.0, 222.0],
                }],
            }
        ],
    }
    html = _render(page, tmp_path)
    assert 'data-kb-ink="caption-fig"' in html
    assert _ink_var(html, "fig-cap0", "--kb-fs") <= 11


def test_figure_note_font_splits_tall_box(tmp_path: Path) -> None:
    """人像图注是窄框多字，不能把整框高当一行去投出大号。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "fig",
                "type": "image",
                "kind": "figure",
                "bbox": [95.0, 253.0, 155.0, 346.0],
                "captions": [
                    {
                        "type": "image_footnote",
                        "text": "康托(G. Cantor, 1845—1918)，德国数学家，集合论创始人.",
                        "bbox": [67.0, 349.0, 183.0, 397.0],
                    }
                ],
            }
        ],
    }
    html = _render(page, tmp_path)
    fs = _fs(html, "fig-cap0")
    assert 7 <= fs <= 11
    assert _ink_var(html, "fig-cap0", "--kb-lh") >= fs


def test_figure_note_not_drowned_by_short_caption(tmp_path: Path) -> None:
    """多行图注用自己摊出来的字号，不被一行图注的票盖回偏大。"""
    short = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "fig-s",
                "type": "image",
                "kind": "figure",
                "bbox": [95.0, 100.0, 155.0, 180.0],
                "captions": [
                    {
                        "type": "image_footnote",
                        "text": "图注",
                        "bbox": [67.0, 190.0, 200.0, 210.0],
                    }
                ],
            }
        ],
    }
    long = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "fig-l",
                "type": "image",
                "kind": "figure",
                "bbox": [95.0, 253.0, 155.0, 346.0],
                "captions": [
                    {
                        "type": "image_footnote",
                        "text": "康托(G. Cantor, 1845—1918)，德国数学家，集合论创始人.",
                        "bbox": [67.0, 349.0, 183.0, 397.0],
                    }
                ],
            }
        ],
    }
    html = _render({**long, "book_pages": [short, long]}, tmp_path)
    assert 7 <= _fs(html, "fig-l-cap0") <= 11


def test_book_pages_vote_on_paint(tmp_path: Path) -> None:
    """全书各页行高在画页取众数；当前页自己的行高不能改全书号。"""
    page = {
        "page_size": [595, 841],
        "book_pages": [
            {
                "blocks": [
                    {
                        "id": "a",
                        "type": "text",
                        "kind": "body",
                        "text": "集合",
                        "bbox": [80.0, 200.0, 200.0, 213.04],
                        "lines": [[{"t": "text", "c": "集合", "bbox": [80.0, 200.0, 200.0, 213.04]}]],
                    }
                ]
            },
            {
                "blocks": [
                    {
                        "id": "b",
                        "type": "text",
                        "kind": "body",
                        "text": "元素",
                        "bbox": [80.0, 200.0, 200.0, 213.04],
                        "lines": [[{"t": "text", "c": "元素", "bbox": [80.0, 200.0, 200.0, 213.04]}]],
                    }
                ]
            },
        ],
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "集合",
                "bbox": [80.0, 200.0, 200.0, 230.0],
                "lines": [[{"t": "text", "c": "集合", "bbox": [80.0, 200.0, 200.0, 230.0]}]],
            }
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'data-kb-ink="body"[^>]*data-kb-block="body"[^>]*>', html)
    assert chunk
    assert "--kb-fs:12" in chunk.group(0)


def test_ink_slot_aside_paints_short_sidebar(tmp_path: Path) -> None:
    """有 slot=aside 的短侧栏着侧栏色，不再被高 28 门槛挡掉。"""
    page = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "q",
                "type": "text",
                "kind": "body",
                "slot": "aside",
                "text": "此处为什么有两种情况？",
                "bbox": [423.0, 491.0, 514.0, 516.0],
                "lines": [{
                    "bbox": [423.0, 491.0, 514.0, 516.0],
                    "spans": [{"t": "text", "c": "此处为什么有两种情况？"}],
                }],
            }
        ],
    }
    html = _render(page, tmp_path)
    chunk = re.search(r'<div class="kb-ir-ink"[^>]*data-kb-block="q"[^>]*>', html)
    assert chunk
    assert 'data-kb-ink="aside"' in chunk.group(0)


def test_ink_no_slot_still_guesses_box(tmp_path: Path) -> None:
    """旧页没有 slot：矮框不猜成侧栏，高窄框仍走框猜。"""
    short = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "q",
                "type": "text",
                "kind": "body",
                "text": "此处为什么有两种情况？",
                "bbox": [423.0, 491.0, 514.0, 516.0],
                "lines": [{
                    "bbox": [423.0, 491.0, 514.0, 516.0],
                    "spans": [{"t": "text", "c": "此处为什么有两种情况？"}],
                }],
            }
        ],
    }
    tall = {
        "page_size": [595, 841],
        "blocks": [
            {
                "id": "aside",
                "type": "text",
                "kind": "body",
                "text": "结合例题，试比较分别用自然语言、列举法、描述法和区间表示集合时，其各自的特点和适用对象。",
                "bbox": [52.0, 200.0, 144.0, 259.0],
                "lines": [{
                    "bbox": [54.0, 202.0, 142.0, 255.82],
                    "spans": [{"t": "text", "c": "结合例题，试比较分别用自然语言、列举法、描述法和区间表示集合时，其各自的特点和适用对象。"}],
                }],
            }
        ],
    }
    short_html = _render(short, tmp_path)
    short_chunk = re.search(r'<div class="kb-ir-ink"[^>]*data-kb-block="q"[^>]*>', short_html)
    assert short_chunk
    assert 'data-kb-ink="aside"' not in short_chunk.group(0)
    tall_html = _render(tall, tmp_path)
    assert 'data-kb-ink="aside"' in tall_html


def test_footnote_uses_own_box_not_book_vote(tmp_path: Path) -> None:
    """同一页的页脚注共用一个字号：取该页能装下所有注的最小号，不跟全书票。"""
    page = {
        "page_size": [595, 841],
        "fonts": {"note": 20, "body": 12, "footer": 12},
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "独立寒秋",
                "bbox": [80.0, 80.0, 400.0, 100.0],
            }
        ],
        "chrome": {
            "footnotes": [
                {
                    "text": "④〔漫江〕满江。漫，遍布。",
                    "bbox": [55.0, 704.0, 176.0, 720.0],
                },
                {
                    "text": (
                        "⑥〔鹰击长空，鱼翔浅底〕鹰在广阔的天上飞，鱼在清澈的水里游。"
                        "击，搏击，这里形容飞得矫健有力。"
                        "翔，本指鸟盘旋飞翔，这里形容鱼游得轻快自由。"
                    ),
                    "bbox": [56.0, 735.0, 279.0, 780.0],
                },
            ],
            "footers": [{"text": "语文 必修上册", "bbox": [80.0, 788.0, 148.0, 802.0]}],
        },
    }
    html = _render(page, tmp_path)
    short_fs = _fs(html, "chrome:footnotes:0")
    tall_fs = _fs(html, "chrome:footnotes:1")
    assert short_fs == tall_fs
    assert 4 <= short_fs <= 9
    assert short_fs != 20
    assert _ink_var(html, "chrome:footnotes:1", "--kb-lh") == 12.5
    chunk = re.search(r'data-kb-block="chrome:footnotes:1"[^>]*>.*?</div>', html, re.S)
    assert chunk
    assert "kb-ir-ink-chrome" in chunk.group(0)
    assert 'data-kb-lines="1"' not in chunk.group(0)
    assert "kb-ir-line" not in chunk.group(0)
    assert "鹰击长空" in chunk.group(0)


def test_footnote_font_can_differ_across_pages(tmp_path: Path) -> None:
    """页脚注不跟全书一张票：只有短注的页可以比有长注的页大一号。"""
    only_short = {
        "page_size": [595, 841],
        "fonts": {"note": 20, "body": 12},
        "blocks": [
            {
                "id": "body",
                "type": "text",
                "kind": "body",
                "text": "独立寒秋",
                "bbox": [80.0, 80.0, 400.0, 100.0],
            }
        ],
        "chrome": {
            "footnotes": [
                {
                    "text": "⑪〔主〕主宰。",
                    "bbox": [308.0, 735.0, 375.0, 750.0],
                },
                {
                    "text": "⑫〔沉浮〕这里指盛衰。",
                    "bbox": [308.0, 750.0, 410.0, 765.0],
                },
            ],
        },
    }
    html = _render(only_short, tmp_path)
    a = _fs(html, "chrome:footnotes:0")
    b = _fs(html, "chrome:footnotes:1")
    assert a == b
    assert a != 20
    assert a >= 8
