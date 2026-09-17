"""page-ir 渲染器（render-page.mjs）的契约测试。

这个渲染器之前一行测试都没有，titleHtml 只读 blk.text 把标题里的公式、下标、
制表位全压成转义字面量的 bug 就是这么活下来的。这里用 node 直接调 renderIrPage。
"""

from __future__ import annotations

import json
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


def test_title_renders_formula_as_mathjax_placeholder(tmp_path: Path) -> None:
    """标题里的行内公式要出 data-latex 占位，不能把 $...$ 原样印出来。"""
    page = _title_page(
        "焦裕禄$^{①}$",
        [[{"t": "text", "c": "焦裕禄"}, {"t": "eq", "c": "^{①}"}]],
    )
    html = _render(page, tmp_path)
    assert 'data-latex="^{①}"' in html
    assert "$^{①}$" not in html


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
