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


def test_table_is_atomic_chunk() -> None:
    md = FIXTURE.read_text(encoding="utf-8")
    _, sections = parse_textbook_markdown(
        md,
        textbook_id="tb:test",
        subject="math",
        grade_band="hs",
        prefix="tb:test",
    )
    table_chunks = [
        ch for s in sections for ch in s.chunks if ch.get("meta", {}).get("doc_type") == "table"
    ]
    assert len(table_chunks) >= 1
    assert "|" in table_chunks[0]["text"]


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
