from __future__ import annotations

from app.kb.markdown import blocks_to_page_markdown
from app.kb.mid import tag_source
from app.kb.prose import fix_mineru_captions, polish_page_prose


PAGE12 = """\
例4 选择适当的方法表示下列集合：

（1）大于0且小于10的全体偶数组成的集合A；

(2) 被 3 除余 2 的所有自然数组成的集合 B;

解 （1）用列举法：A=\\{2,4,6,8\\}.

![插图](/api/v1/kb/proxy/asset?key=kb%2Fpdfs%2Fb1%2Fshot.jpg)

C = \\{(x, y) | x y <   0 \\}.

![{'type': 'equation_inline', 'content': '[a,b]'}](/api/v1/kb/proxy/asset?key=kb%2Fpdfs%2Fb1%2Flabel.jpg)

*{'type': 'equation_inline', 'content': '[a,b]'}*

![{'type': 'text', 'content': '(a,b)'}
{'type': 'text', 'content': '图1-1-1'}](/api/v1/kb/proxy/asset?key=kb%2Fpdfs%2Fb1%2Faxis.jpg)

*{'type': 'text', 'content': '(a,b)'}
{'type': 'text', 'content': '图1-1-1'}*

#

解 (1)(2)(3)(4)(6)都是真命题；(5)是假命题.
"""


def test_caption_picks_figure_over_interval_dicts():
    out = fix_mineru_captions(PAGE12)
    assert "图1-1-1" in out
    assert "{'type'" not in out
    assert "![图1-1-1](" in out


def test_polish_page12_like_studio_prose():
    out = polish_page_prose(PAGE12)
    assert "### 例4 选择适当的方法表示下列集合：" in out
    assert 'class="kb-' not in out
    assert "(2) 被 3" in out
    assert "（2）被 3" in tag_source(out)
    assert "shot.jpg" not in out
    assert "label.jpg" in out
    assert "axis.jpg" in out
    assert "*图1-1-1*" in out
    assert "{'type'" not in out
    assert not any(ln.strip() == "#" for ln in out.splitlines())
    assert "(1)(2)(3)(4)(6)都是真命题" in out
    assert "（6）都是真命题" not in out
    tagged = tag_source(out)
    assert tagged.count("::: kb-list-item") == 3
    assert "::: kb-solution" in tagged


def test_new_import_heading_and_skip_hash():
    md = blocks_to_page_markdown(
        [
            {"type": "text", "text": "1.1 集合初步"},
            {"type": "text", "text": "#"},
            {"type": "text", "text": "（1）大于0的偶数"},
        ]
    )
    assert "### 1.1 集合初步" in md
    assert md.count("#") == 3
    assert 'class="kb-' not in md
    assert "::: kb-list-item" in tag_source(md)


def test_does_not_split_function_or_figure_refs():
    out = polish_page_prose(
        "因为f(3)=7>0, f(4)=-40<0，所以此函数有零点.\n\n"
        "图 1-1-4(1) 表示相交；图 1-1-4(2) 表示包含；图 1-1-4(3) 表示不相交."
    )
    assert "kb-list-item" not in out
    assert "f(3)=7" in out
    assert "图 1-1-4(1)" in out


def test_drops_spaced_tex_formula_shot():
    out = polish_page_prose(
        "![插图](/x.jpg)\n\na _ {1} x ^ {2} + b _ {1} x = 0\n"
    )
    assert "x.jpg" not in out
    assert "a _ {1}" in out


def test_portrait_caption_is_visible_under_image():
    out = polish_page_prose(
        "![{'type': 'text', 'content': '康托(G. Cantor, 1845—1918)，德国数学家，集合论创始人.'}](/cantor.jpg)\n"
    )
    assert "![康托" in out
    assert "*康托(G. Cantor, 1845—1918)，德国数学家，集合论创始人.*" in out


def test_keeps_portrait_caption_and_exercise_heading():
    out = polish_page_prose(
        "![{'type': 'text', 'content': '纳皮尔(J. Napier, 1550—1617)，苏格兰数学家.'}](/p.jpg)\n\n"
        "练习 2.1(2)\n\n"
        "1. 判断下列各组对象能否组成集合。\n\n"
        "解（1）这是无限集.\n"
    )
    assert "纳皮尔" in out
    assert "### 练习 2.1(2)" in out
    assert 'class="kb-' not in out
    tagged = tag_source(out)
    assert "::: kb-list-item" in tagged
    assert "::: kb-solution" in tagged


def test_chapter_title_after_splash_image():
    out = polish_page_prose("![插图](/c.jpg)\n\n等式与不等式\n\n数量关系是数学重要的研究对象。")
    assert "### 等式与不等式" in out


def test_named_theorem_becomes_lead():
    out = polish_page_prose("定理 如果 f(a)·f(b)<0，那么一定有零点.")
    assert 'class="kb-' not in out
    assert "一定有零点" in out
    assert "::: kb-lead" in tag_source(out)
