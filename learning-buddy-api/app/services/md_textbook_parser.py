"""Legacy regex textbook markdown parser — fallback when blocks.json is missing.

Prefer ``corpus_tree_from_ir.build_sections_from_ir`` for new import jobs.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from app.services.corpus_locale import is_english_subject

_HEADING_MD_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_CHAPTER_RE = re.compile(r"^第[０-９0-9一二三四五六七八九十百千]+章\s*.+")
_LESSON_RE = re.compile(r"^[０-９0-9]+[\.．][０-９0-9]+\s+\S")
_COVER_NOISE_RE = re.compile(r"定价|易文网|ISBN|版权所有|上海教育出版社")


@dataclass
class ParsedSection:
    section_id: str
    label: str
    summary: str
    text: str
    level: int
    parent_section_id: str = ""
    chunks: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def _slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text.strip().lower())[:max_len]
    return s.strip("-") or "section"


def _is_chapter_line(line: str) -> bool:
    s = line.strip()
    if _HEADING_MD_RE.match(s):
        title = _HEADING_MD_RE.match(s).group(2).strip()  # type: ignore[union-attr]
        return bool(_CHAPTER_RE.match(title))
    return bool(_CHAPTER_RE.match(s))


def _is_lesson_line(line: str) -> bool:
    s = line.strip()
    if _HEADING_MD_RE.match(s):
        title = _HEADING_MD_RE.match(s).group(2).strip()  # type: ignore[union-attr]
        return bool(_LESSON_RE.match(title))
    return bool(_LESSON_RE.match(s))


def _line_title(line: str) -> str:
    m = _HEADING_MD_RE.match(line.strip())
    return m.group(2).strip() if m else line.strip()


def _chunk_section_body(body: str, section: ParsedSection, prefix: str) -> list[dict]:
    """One toc_leaf chunk per markdown section (directory authority; no char window)."""
    if not body.strip():
        return []
    return [
        {
            "id": f"chunk:{prefix}:{_slug(section.label)}:0",
            "label": section.label,
            "summary": body[:240],
            "text": body,
            "section_id": section.section_id,
            "meta": {"doc_type": "chunk", "chunk_kind": "toc_leaf"},
        }
    ]


def _skip_cover_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    started = False
    for line in lines:
        if not started and _COVER_NOISE_RE.search(line):
            continue
        if _is_chapter_line(line) or _is_lesson_line(line):
            started = True
        if started or len(out) > 0:
            out.append(line)
    return out if started else lines


def parse_textbook_markdown(
    markdown: str,
    *,
    textbook_id: str,
    subject: str,
    grade_band: str,
    prefix: str = "",
    textbook_label: str = "",
) -> tuple[dict, list[ParsedSection]]:
    """Parse MD into textbook + hierarchical sections (chapter → lesson) with chunks."""
    en = is_english_subject(subject)
    default_tb = "Imported textbook" if en else "导入教材"
    lines = _skip_cover_lines(markdown.splitlines())

    sections: list[ParsedSection] = []
    current_chapter: ParsedSection | None = None
    current_lesson: ParsedSection | None = None
    body_lines: list[str] = []

    def flush_lesson() -> None:
        nonlocal current_lesson, body_lines
        if current_lesson is None:
            body_lines = []
            return
        body = "\n".join(body_lines).strip()
        if not body:
            current_lesson = None
            body_lines = []
            return
        current_lesson.text = body
        current_lesson.summary = body[:240] if body else current_lesson.label
        current_lesson.chunks = _chunk_section_body(
            body,
            current_lesson,
            prefix or textbook_id,
        )
        sections.append(current_lesson)
        current_lesson = None
        body_lines = []

    def flush_chapter() -> None:
        nonlocal current_chapter
        flush_lesson()
        if current_chapter is not None:
            sections.append(current_chapter)
            current_chapter = None

    def _ensure_lesson() -> None:
        nonlocal current_lesson
        if current_lesson is not None or current_chapter is None:
            return
        sid = f"sec:{prefix or textbook_id}:ch:{_slug(current_chapter.label)}:body"
        current_lesson = ParsedSection(
            section_id=sid,
            label=f"{current_chapter.label} · {'Overview' if en else '概要'}",
            summary=current_chapter.label,
            text="",
            level=2,
            parent_section_id=current_chapter.section_id,
            meta={"doc_type": "section", "section_kind": "lesson", "locale_en": en},
        )

    for line in lines:
        if _is_chapter_line(line):
            flush_chapter()
            title = _line_title(line)
            sid = f"sec:{prefix or textbook_id}:ch:{_slug(title)}"
            current_chapter = ParsedSection(
                section_id=sid,
                label=title,
                summary=title,
                text="",
                level=1,
                meta={"doc_type": "section", "section_kind": "chapter", "locale_en": en},
            )
            continue
        if _is_lesson_line(line):
            flush_lesson()
            title = _line_title(line)
            parent_id = current_chapter.section_id if current_chapter else ""
            sid = f"sec:{prefix or textbook_id}:ls:{_slug(title)}"
            current_lesson = ParsedSection(
                section_id=sid,
                label=title,
                summary=title,
                text="",
                level=2,
                parent_section_id=parent_id,
                meta={"doc_type": "section", "section_kind": "lesson", "locale_en": en},
            )
            body_lines = []
            continue
        m = _HEADING_MD_RE.match(line.strip())
        if m and len(m.group(1)) <= 2:
            title = m.group(2).strip()
            if _CHAPTER_RE.match(title) or _LESSON_RE.match(title):
                continue
        if not line.strip():
            if current_lesson is not None:
                body_lines.append(line)
            continue
        if current_chapter is not None or current_lesson is not None:
            if current_lesson is None:
                _ensure_lesson()
            body_lines.append(line)

    flush_chapter()

    tb_label = textbook_label or default_tb
    if not textbook_id:
        textbook_id = f"tb:import:{subject}:{grade_band}:{uuid.uuid4().hex[:8]}"

    if not sections and markdown.strip():
        sid = f"sec:{textbook_id}:body"
        fallback = ParsedSection(
            section_id=sid,
            label="Body" if en else "正文",
            summary=markdown[:200],
            text=markdown.strip(),
            level=1,
            meta={"doc_type": "section", "section_kind": "body", "locale_en": en},
        )
        fallback.chunks = _chunk_section_body(markdown, fallback, prefix or textbook_id)
        sections = [fallback]

    textbook = {
        "id": textbook_id,
        "label": tb_label,
        "summary": tb_label[:200],
        "text": tb_label,
        "tags": [f"grade_band:{grade_band}", f"subject:{subject}"],
        "links": {"subject": subject, "grade_band": grade_band},
    }
    return textbook, sections
