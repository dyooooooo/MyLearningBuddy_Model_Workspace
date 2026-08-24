from __future__ import annotations

from pathlib import Path

from app.services.md_textbook_parser import parse_textbook_markdown

FIXTURE = Path(__file__).resolve().parents[1] / "data/eval/corpus-import/sample-chapter.md"


def test_parse_chapters_and_lessons() -> None:
    md = FIXTURE.read_text(encoding="utf-8")
    tb, sections = parse_textbook_markdown(
        md,
        textbook_id="tb:test:math:hs:sample",
        subject="math",
        grade_band="hs",
        prefix="tb:test:math:hs:sample",
        textbook_label="测试教材",
    )
    assert tb["label"] == "测试教材"
    chapters = [s for s in sections if s.meta.get("section_kind") == "chapter"]
    lessons = [s for s in sections if s.meta.get("section_kind") == "lesson"]
    assert len(chapters) >= 2
    assert len(lessons) >= 2
    assert all(len(s.chunks) >= 1 for s in lessons)


def test_table_stays_whole_inside_its_section_chunk() -> None:
    """现在每个小节只出一个 `toc_leaf` 分块（目录定权），表格不能被切开。"""
    md = FIXTURE.read_text(encoding="utf-8")
    _, sections = parse_textbook_markdown(
        md,
        textbook_id="tb:test",
        subject="math",
        grade_band="hs",
        prefix="tb:test",
    )
    chunks_with_table = [
        ch for s in sections for ch in s.chunks if "| N | 自然数集 |" in ch["text"]
    ]
    assert len(chunks_with_table) == 1
    text = chunks_with_table[0]["text"]
    assert "| 集合 | 含义 |" in text
    assert "| Z | 整数集 |" in text
    assert chunks_with_table[0]["meta"]["chunk_kind"] == "toc_leaf"


def test_no_cover_pricing_in_chunks() -> None:
    md = "定价：11.90元\n\n第1章 集合与逻辑\n\n1.1 集合\n\n正文内容。"
    _, sections = parse_textbook_markdown(
        md,
        textbook_id="tb:t",
        subject="math",
        grade_band="hs",
        prefix="tb:t",
    )
    all_text = " ".join(ch["text"] for s in sections for ch in s.chunks)
    assert "11.90" not in all_text
