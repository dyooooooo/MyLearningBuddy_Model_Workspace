"""Unit tests for IR-based corpus tree building (TOC leaf = one chunk)."""
from __future__ import annotations

from app.services.corpus_tree_from_ir import (
    build_sections_from_ir,
    section_for_page,
)
from app.services.document_ir import DocumentBlock


def _sample_blocks() -> list[DocumentBlock]:
    return [
        DocumentBlock(
            id="ch1",
            type="title",
            page_no=10,
            order=1,
            text="第一章 集合与常用逻辑用语",
            level=1,
        ),
        DocumentBlock(
            id="ls11",
            type="heading",
            page_no=10,
            order=2,
            text="1.1 集合初步",
            level=2,
        ),
        DocumentBlock(
            id="p1",
            type="paragraph",
            page_no=10,
            order=3,
            text="集合是研究对象的总称。" * 80,
        ),
        DocumentBlock(
            id="tbl",
            type="table",
            page_no=11,
            order=4,
            table_html="<table><tr><td>表1-1</td></tr></table>",
            caption="表 1-1",
        ),
        DocumentBlock(
            id="ex",
            type="paragraph",
            page_no=11,
            order=5,
            text="例2 设 A={1,2,3}，求 |A|。",
            meta={"atomic_example": True},
        ),
        DocumentBlock(
            id="fig",
            type="figure",
            page_no=11,
            order=6,
            asset_url="/api/v1/studio/proxy/import/asset?key=fig.jpg",
            caption="图 1-1",
        ),
    ]


def test_build_sections_chapter_lesson_one_toc_leaf():
    blocks = _sample_blocks()
    textbook, sections = build_sections_from_ir(
        blocks,
        textbook_id="tb:test",
        subject="math",
        grade_band="hs",
        textbook_label="数学必修第一册",
    )
    assert textbook["id"] == "tb:test"
    kinds = [s.meta.get("section_kind") for s in sections]
    assert "chapter" in kinds
    assert "lesson" in kinds
    lesson = next(s for s in sections if s.meta.get("section_kind") == "lesson")
    assert lesson.label == "1.1 集合初步"
    assert len(lesson.chunks) == 1
    ch = lesson.chunks[0]
    assert ch["meta"]["chunk_kind"] == "toc_leaf"
    assert "例2" in ch["text"]
    assert "<table" in ch["text"] or "表" in ch["text"]


def test_section_for_page_by_start_page():
    blocks = _sample_blocks()
    _, sections = build_sections_from_ir(
        blocks,
        textbook_id="tb:test",
        subject="math",
        grade_band="hs",
    )
    sec_id = section_for_page(sections, 10)
    assert sec_id is not None
    lesson = next(s for s in sections if s.section_id == sec_id)
    assert lesson.meta.get("section_kind") == "lesson"
    assert section_for_page(sections, 11) == sec_id


def test_block_conservation_in_toc_leaf():
    blocks = _sample_blocks()
    _, sections = build_sections_from_ir(
        blocks,
        textbook_id="tb:test",
        subject="math",
        grade_band="hs",
    )
    lesson = next(s for s in sections if s.meta.get("section_kind") == "lesson")
    covered_ids: set[str] = set()
    for ch in lesson.chunks:
        covered_ids.update(ch.get("meta", {}).get("block_ids") or [])
    lesson_block_ids = {b.id for b in lesson.blocks}
    assert lesson_block_ids.issubset(covered_ids)


def test_preface_and_toc_are_separate_sections():
    """章前 orphan：前言与目录拆开；目录页可被 section_for_page 命中。"""
    blocks = [
        DocumentBlock(
            id="pf_h",
            type="heading",
            page_no=4,
            order=1,
            text="前言",
            level=1,
        ),
        DocumentBlock(
            id="pf_p",
            type="paragraph",
            page_no=4,
            order=2,
            text="认真学习数学。",
        ),
        DocumentBlock(
            id="toc_h",
            type="heading",
            page_no=6,
            order=1,
            text="目录",
            level=1,
        ),
        DocumentBlock(
            id="toc_l",
            type="list",
            page_no=6,
            order=2,
            text="1.1 集合初步 2",
            meta={"pedagogy_role": "toc", "toc_entries": [{"title": "1.1 集合初步", "page": 2}]},
        ),
        DocumentBlock(
            id="toc_l2",
            type="list",
            page_no=7,
            order=1,
            text="1.2 常用逻辑用语 13",
            meta={"pedagogy_role": "toc"},
        ),
        DocumentBlock(
            id="intro",
            type="paragraph",
            page_no=8,
            order=1,
            text="集合是现代数学语言的重要组成部分。",
        ),
        DocumentBlock(
            id="ch1",
            type="title",
            page_no=9,
            order=1,
            text="第一章 集合与常用逻辑用语",
            level=1,
        ),
        DocumentBlock(
            id="ls11",
            type="heading",
            page_no=9,
            order=2,
            text="1.1 集合初步",
            level=2,
        ),
        DocumentBlock(
            id="body",
            type="paragraph",
            page_no=9,
            order=3,
            text="集合是研究对象的总称。" * 40,
        ),
    ]
    _, sections = build_sections_from_ir(
        blocks,
        textbook_id="tb:test",
        subject="math",
        grade_band="hs",
        textbook_label="数学必修第一册",
    )
    kinds = [s.meta.get("section_kind") for s in sections]
    assert "preface" in kinds
    assert "toc" in kinds
    preface = next(s for s in sections if s.meta.get("section_kind") == "preface")
    toc = next(s for s in sections if s.meta.get("section_kind") == "toc")
    assert preface.label == "前言"
    assert toc.label == "目录"
    assert {b.page_no for b in preface.blocks} == {4}
    assert {b.page_no for b in toc.blocks} == {6, 7}
    assert section_for_page(sections, 4) == preface.section_id
    assert section_for_page(sections, 6) == toc.section_id
    assert section_for_page(sections, 7) == toc.section_id
    # leftover p8 attached to first lesson, not under 目录
    lesson = next(s for s in sections if s.meta.get("section_kind") == "lesson")
    assert any(b.id == "intro" for b in lesson.blocks)
    assert not any(b.id == "intro" for b in toc.blocks)
