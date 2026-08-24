from __future__ import annotations

from app.kb.math_text import wrap_bare_math


def test_wraps_mixed_cjk_and_bare_latex():
    out = wrap_bare_math("不等式2x + 1 \\leqslant 0的所有解")
    assert "$2x + 1 \\leqslant 0$" in out
    assert "不等式" in out
    assert "的所有解" in out


def test_wraps_membership():
    out = wrap_bare_math("就记作a \\in A，读作“a 属于 A”")
    assert "$a \\in A$" in out


def test_leaves_delimited_math():
    src = "已知 $x \\leqslant 0$ 成立"
    assert wrap_bare_math(src) == src


def test_skips_table_html():
    src = "<table><tr><td>N</td></tr></table>"
    assert wrap_bare_math(src) == src
