from __future__ import annotations

from app.kb.mid import block_is_centered, strip_skin, tag_source


def test_strip_skin_list_and_toc() -> None:
    raw = (
        '<p class="kb-list-item">（1）大于0且小于10的全体偶数组成的集合A；</p>\n'
        '<div class="kb-page-toc-heading">目录</div>'
        '<nav class="kb-page-toc"><ol class="kb-page-toc-list">'
        '<li class="kb-page-toc-row"><span class="kb-page-toc-title">1.1 集合初步</span>'
        '<span class="kb-page-toc-page">2</span></li></ol></nav>'
    )
    out = strip_skin(raw)
    assert 'class="kb-' not in out
    assert "（1）大于0且小于10的全体偶数组成的集合A；" in out
    assert "目录" in out
    assert "1.1 集合初步 2" in out
    glued = strip_skin(
        '<div class="kb-page-toc-heading">Contents</div>'
        '<nav class="kb-page-toc"><ol>'
        '<li><span class="kb-page-toc-title">1 The media</span></li></ol></nav>'
    )
    assert "Contents1" not in glued
    assert "Contents" in glued
    assert "1 The media" in glued
    assert "Contents1" not in strip_skin("Contents1 The media")


def test_tag_source_writes_fences_not_html() -> None:
    md = (
        "例4 选择适当的方法表示下列集合：\n\n"
        "（1）大于0且小于10的全体偶数组成的集合A；\n\n"
        "(2) 被 3 除余 2 的所有自然数组成的集合 B;\n\n"
        "解 （1）用列举法：A=\\{2,4,6,8\\}.\n"
    )
    tagged = tag_source(md)
    assert 'class="kb-' not in tagged
    assert "::: kb-list-item" in tagged
    assert "::: kb-solution" in tagged
    assert "### 例4" in tagged


def test_tag_source_toc_and_table() -> None:
    md = "目录\n第1章 集合\n1.1 集合初步 2\n"
    tagged = tag_source(md)
    assert "::: kb-page-toc" in tagged
    hashed = "目录\n### 第1章 集合\n### 1.1 集合初步 2\n"
    assert "::: kb-page-toc" in tag_source(hashed)
    assert "###" not in tag_source(hashed)
    table = tag_source('<table><tr><td>数集</td><td>符号</td></tr></table>')
    assert "::: kb-table-wrap" in table
    assert 'class="kb-table-wrap"' not in table


def test_bbox_center() -> None:
    assert block_is_centered([300, 80, 700, 120], page_width=1000.0)
    assert not block_is_centered([40, 80, 960, 120], page_width=1000.0)
    assert not block_is_centered([40, 80, 400, 120], page_width=1000.0)
    assert block_is_centered([0.3, 0.1, 0.7, 0.2])
