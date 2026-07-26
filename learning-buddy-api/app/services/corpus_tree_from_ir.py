"""Build corpus tree (sections + semantic chunks) from DocumentBlock IR."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from app.services.document_ir import (
    DocumentBlock,
    html_to_plain_text,
    is_chapter_text,
    is_layout_atomic,
    is_lesson_text,
    layout_zone,
    normalize_chapter_title,
    render_block_html,
    render_block_markdown,
)
from app.services.corpus_locale import is_english_subject

# Legacy char-window chunking (NOT textbook authority). Kept for rare callers;
# build_sections_from_ir uses toc_leaf (one chunk per directory leaf).
TARGET_CHUNK_MIN = 600
TARGET_CHUNK_MAX = 1200

_EXAMPLE_ATOMIC_RE = re.compile(r"^(例\s*[０-９0-9]+|练习\s*[\d.]+|复习题|内容提要|犃组|B组|A组)")
_TOC_TITLE_RE = re.compile(r"^目\s*录$")
_PREFACE_TITLE_RE = re.compile(r"^前\s*言$")


def _slug(text: str, max_len: int = 32) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", text.strip().lower())[:max_len]
    return s.strip("-") or "section"


def _block_plain_title(block: DocumentBlock) -> str:
    return (block.text or block.caption or "").strip()


def _is_toc_block(block: DocumentBlock) -> bool:
    meta = block.meta or {}
    if meta.get("pedagogy_role") == "toc":
        return True
    if meta.get("toc_entries"):
        return True
    if block.type in ("title", "heading") and _TOC_TITLE_RE.match(_block_plain_title(block)):
        return True
    return False


def _split_orphan_front_matter(
    orphan_blocks: list[DocumentBlock],
) -> tuple[list[DocumentBlock], list[DocumentBlock], list[DocumentBlock]]:
    """Split pre-chapter orphans into preface / TOC / leftover (chapter intro).

    目录 must be its own section so Studio can open it and jump via TOC rows.
    """
    if not orphan_blocks:
        return [], [], []
    toc_pages = {
        int(b.page_no)
        for b in orphan_blocks
        if _is_toc_block(b) and int(b.page_no or 0) > 0
    }
    if not toc_pages:
        return list(orphan_blocks), [], []
    first_toc = min(toc_pages)
    last_toc = max(toc_pages)
    # Include whole pages between first/last TOC (page_number / running_header)
    preface = [b for b in orphan_blocks if int(b.page_no or 0) < first_toc]
    toc = [b for b in orphan_blocks if first_toc <= int(b.page_no or 0) <= last_toc]
    leftover = [b for b in orphan_blocks if int(b.page_no or 0) > last_toc]
    return preface, toc, leftover


def _preface_label(blocks: list[DocumentBlock], *, en: bool) -> str:
    for b in blocks:
        if b.type in ("title", "heading") and _PREFACE_TITLE_RE.match(_block_plain_title(b)):
            return "Front matter" if en else "前言"
    return "Front matter" if en else "前言"


@dataclass
class IrSection:
    section_id: str
    label: str
    summary: str
    text: str
    level: int
    parent_section_id: str = ""
    start_page: int = 1
    blocks: list[DocumentBlock] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def _is_atomic_block(block: DocumentBlock) -> bool:
    if is_layout_atomic(block):
        return True
    if block.type in ("table", "figure", "equation"):
        return True
    if block.meta.get("atomic_example"):
        return True
    text = (block.text or block.caption or "").strip()
    return bool(_EXAMPLE_ATOMIC_RE.match(text))


def _use_html_chunks(blocks: list[DocumentBlock]) -> bool:
    return any((b.meta or {}).get("layout_zone") for b in blocks)


def _block_text_for_merge(block: DocumentBlock, *, as_html: bool) -> str:
    if as_html:
        return render_block_html(block)
    return render_block_markdown(block)


def _merge_text_parts(parts: list[str], *, as_html: bool) -> str:
    sep = "\n" if as_html else "\n\n"
    return sep.join(p for p in parts if p.strip())


def _toc_leaf_chunk(section: IrSection, prefix: str) -> list[dict]:
    """Exactly one retrieval chunk per TOC leaf section (directory = chunk boundary)."""
    blocks = section.blocks
    as_html = _use_html_chunks(blocks) if blocks else False
    if blocks:
        text = _merge_text_parts(
            [_block_text_for_merge(b, as_html=as_html) for b in blocks],
            as_html=as_html,
        ).strip()
        block_ids = [b.id for b in blocks]
        pages = sorted({b.page_no for b in blocks})
    else:
        text = (section.text or "").strip()
        block_ids = []
        pages = [section.start_page] if section.start_page else []
    if not text and not block_ids:
        return []
    plain = html_to_plain_text(text) if as_html or (text.startswith("<") and "lb-" in text) else text
    summary = (plain or section.label or "")[:240]
    return [
        {
            "id": f"chunk:{prefix}:{_slug(section.label)}:0",
            "label": section.label,
            "summary": summary,
            "text": text or section.label,
            "section_id": section.section_id,
            "meta": {
                "doc_type": "chunk",
                "chunk_kind": "toc_leaf",
                "block_ids": block_ids,
                "source_pages": pages,
                "content_format": "html" if as_html else "markdown",
            },
        }
    ]


def finalize_leaf_section(section: IrSection, prefix: str) -> None:
    """Fill section text/summary and attach a single toc_leaf chunk."""
    as_html = _use_html_chunks(section.blocks) if section.blocks else False
    if section.blocks:
        section.text = _merge_text_parts(
            [_block_text_for_merge(b, as_html=as_html) for b in section.blocks],
            as_html=as_html,
        ).strip()
    plain = (
        html_to_plain_text(section.text)
        if section.text and (as_html or section.text.strip().startswith("<"))
        else (section.text or "")
    )
    section.summary = (plain or section.label)[:240]
    section.chunks = _toc_leaf_chunk(section, prefix)


def _merge_blocks_to_chunks(
    blocks: list[DocumentBlock],
    section: IrSection,
    prefix: str,
) -> list[dict]:
    """Legacy char-window chunks — not used for textbook TOC authority path."""
    if not blocks:
        return []

    as_html = _use_html_chunks(blocks)
    en = section.meta.get("locale_en", False)
    part_label = "Part" if en else "段"
    chunks: list[dict] = []
    buf_blocks: list[DocumentBlock] = []
    buf_len = 0

    def flush_buf() -> None:
        nonlocal buf_blocks, buf_len
        if not buf_blocks:
            return
        text = _merge_text_parts([_block_text_for_merge(b, as_html=as_html) for b in buf_blocks], as_html=as_html)
        if not text:
            buf_blocks = []
            buf_len = 0
            return
        idx = len(chunks)
        block_ids = [b.id for b in buf_blocks]
        pages = sorted({b.page_no for b in buf_blocks})
        linked = [
            b.meta.get("anchor_block_id")
            for b in buf_blocks
            if b.meta.get("anchor_block_id")
        ]
        chunk_meta: dict = {
            "doc_type": "chunk",
            "chunk_kind": "semantic",
            "block_ids": block_ids,
            "source_pages": pages,
            "content_format": "html" if as_html else "markdown",
        }
        if linked:
            chunk_meta["linked_block_ids"] = list(dict.fromkeys(str(x) for x in linked if x))
        summary = html_to_plain_text(text)[:240] if as_html else text[:240]
        chunks.append(
            {
                "id": f"chunk:{prefix}:{_slug(section.label)}:{idx}",
                "label": f"{section.label} · {part_label}{idx + 1}",
                "summary": summary,
                "text": text,
                "section_id": section.section_id,
                "meta": chunk_meta,
            }
        )
        buf_blocks = []
        buf_len = 0

    for block in blocks:
        zone = layout_zone(block)
        if zone in ("aside_right", "aside_left") and not _is_atomic_block(block):
            flush_buf()
            text = _block_text_for_merge(block, as_html=as_html)
            idx = len(chunks)
            anchor = block.meta.get("anchor_block_id")
            chunk_meta = {
                "doc_type": "chunk",
                "chunk_kind": "semantic",
                "block_ids": [block.id],
                "source_pages": [block.page_no],
                "atomic": True,
                "layout_zone": zone,
                "content_format": "html" if as_html else "markdown",
            }
            if anchor:
                chunk_meta["linked_block_ids"] = [str(anchor)]
            summary = html_to_plain_text(text)[:240] if as_html else text[:240]
            chunks.append(
                {
                    "id": f"chunk:{prefix}:{_slug(section.label)}:{idx}",
                    "label": f"{section.label} · {'边注' if not en else 'Aside'}{idx + 1}",
                    "summary": summary,
                    "text": text,
                    "section_id": section.section_id,
                    "meta": chunk_meta,
                }
            )
            continue

        if _is_atomic_block(block):
            flush_buf()
            text = _block_text_for_merge(block, as_html=as_html)
            label_suffix = {"table": "表", "figure": "图", "equation": "公式"}.get(block.type, "块")
            idx = len(chunks)
            summary = html_to_plain_text(text)[:240] if as_html else text[:240]
            chunks.append(
                {
                    "id": f"chunk:{prefix}:{_slug(section.label)}:{idx}",
                    "label": f"{section.label} · {label_suffix}{idx + 1}",
                    "summary": summary,
                    "text": text,
                    "section_id": section.section_id,
                    "meta": {
                        "doc_type": "chunk",
                        "chunk_kind": "semantic",
                        "block_ids": [block.id],
                        "source_pages": [block.page_no],
                        "atomic": True,
                        "content_format": "html" if as_html else "markdown",
                    },
                }
            )
            continue

        if zone == "main" or zone == "full_bleed" or not zone:
            piece = _block_text_for_merge(block, as_html=as_html)
        else:
            continue
        piece_len = len(piece)
        if buf_blocks and buf_len + piece_len + 2 > TARGET_CHUNK_MAX:
            flush_buf()
        buf_blocks.append(block)
        buf_len += piece_len + 2
        if buf_len >= TARGET_CHUNK_MIN:
            flush_buf()

    flush_buf()
    return chunks


def _refine_chunks_for_section(
    refine_chunks: list[dict],
    blocks: list[DocumentBlock],
) -> list[dict]:
    """Keep only refine chunks whose blocks belong to this section."""
    if not refine_chunks or not blocks:
        return []
    block_ids = {b.id for b in blocks}
    pages = {b.page_no for b in blocks}
    out: list[dict] = []
    for rc in refine_chunks:
        ids = [str(x) for x in (rc.get("block_ids") or [])]
        page_no = rc.get("page_no")
        if page_no is not None and int(page_no) in pages:
            out.append(rc)
            continue
        if any(i in block_ids for i in ids):
            out.append(rc)
    return out


def _chunks_from_refine(
    blocks: list[DocumentBlock],
    section: IrSection,
    prefix: str,
    refine_chunks: list[dict],
) -> list[dict]:
    """Build section chunks from 35B refine chunk list."""
    if not blocks or not refine_chunks:
        return []

    block_ids = {b.id for b in blocks}
    blocks_by_id = {b.id: b for b in blocks}
    as_html = _use_html_chunks(blocks)
    en = section.meta.get("locale_en", False)
    chunks: list[dict] = []
    used_ids: set[str] = set()

    for idx, rc in enumerate(refine_chunks):
        ids = [str(x) for x in (rc.get("block_ids") or [])]
        if not ids or not any(i in block_ids for i in ids):
            continue
        ordered = [blocks_by_id[i] for i in ids if i in blocks_by_id]
        if not ordered:
            continue
        for b in ordered:
            used_ids.add(b.id)
        text = _merge_text_parts(
            [_block_text_for_merge(b, as_html=as_html) for b in ordered],
            as_html=as_html,
        )
        if not text.strip():
            continue
        label = str(rc.get("label") or f"{section.label} · 段{idx + 1}")
        chunk_id = str(rc.get("chunk_id") or f"chunk:{prefix}:{_slug(section.label)}:{idx}")
        pages = sorted({b.page_no for b in ordered})
        anchor = rc.get("anchor")
        chunk_meta: dict = {
            "doc_type": "chunk",
            "chunk_kind": "semantic",
            "block_ids": [b.id for b in ordered],
            "source_pages": pages,
            "content_format": "html" if as_html else "markdown",
            "refine_chunk": True,
        }
        if anchor:
            chunk_meta["anchor"] = anchor
        summary = html_to_plain_text(text)[:240] if as_html else text[:240]
        chunks.append(
            {
                "id": chunk_id if chunk_id.startswith("chunk:") else f"chunk:{prefix}:{chunk_id}",
                "label": label,
                "summary": summary,
                "text": text,
                "section_id": section.section_id,
                "meta": chunk_meta,
            }
        )

    for block in blocks:
        if block.id in used_ids:
            continue
        zone = layout_zone(block)
        if zone in ("aside_right", "aside_left") and not _is_atomic_block(block):
            text = _block_text_for_merge(block, as_html=as_html)
            anchor = block.meta.get("anchor_block_id")
            chunk_meta = {
                "doc_type": "chunk",
                "chunk_kind": "semantic",
                "block_ids": [block.id],
                "source_pages": [block.page_no],
                "atomic": True,
                "layout_zone": zone,
                "content_format": "html" if as_html else "markdown",
            }
            if anchor:
                chunk_meta["linked_block_ids"] = [str(anchor)]
            summary = html_to_plain_text(text)[:240] if as_html else text[:240]
            chunks.append(
                {
                    "id": f"chunk:{prefix}:{_slug(section.label)}:aside-{len(chunks)}",
                    "label": f"{section.label} · {'边注' if not en else 'Aside'}{len(chunks) + 1}",
                    "summary": summary,
                    "text": text,
                    "section_id": section.section_id,
                    "meta": chunk_meta,
                }
            )
            used_ids.add(block.id)
            continue
        if _is_atomic_block(block):
            text = _block_text_for_merge(block, as_html=as_html)
            label_suffix = {"table": "表", "figure": "图", "equation": "公式"}.get(block.type, "块")
            summary = html_to_plain_text(text)[:240] if as_html else text[:240]
            chunks.append(
                {
                    "id": f"chunk:{prefix}:{_slug(section.label)}:atomic-{len(chunks)}",
                    "label": f"{section.label} · {label_suffix}{len(chunks) + 1}",
                    "summary": summary,
                    "text": text,
                    "section_id": section.section_id,
                    "meta": {
                        "doc_type": "chunk",
                        "chunk_kind": "semantic",
                        "block_ids": [block.id],
                        "source_pages": [block.page_no],
                        "atomic": True,
                        "content_format": "html" if as_html else "markdown",
                    },
                }
            )
            used_ids.add(block.id)

    return chunks


def build_sections_from_ir(
    blocks: list[DocumentBlock],
    *,
    textbook_id: str,
    subject: str,
    grade_band: str,
    textbook_label: str = "",
    refine_chunks: list[dict] | None = None,
) -> tuple[dict, list[IrSection]]:
    """Partition IR blocks into chapter/lesson sections; one toc_leaf chunk per leaf.

    ``refine_chunks`` is ignored for chunk boundaries (TOC is authority). Kept in
    the signature for call-site compatibility.
    """
    del refine_chunks  # TOC leaf path — do not invent parallel chunk trees
    en = is_english_subject(subject)
    prefix = textbook_id
    sections: list[IrSection] = []
    current_chapter: IrSection | None = None
    current_lesson: IrSection | None = None
    orphan_blocks: list[DocumentBlock] = []

    def flush_lesson() -> None:
        nonlocal current_lesson
        if current_lesson is None:
            return
        finalize_leaf_section(current_lesson, prefix)
        sections.append(current_lesson)
        current_lesson = None

    def flush_chapter() -> None:
        nonlocal current_chapter
        flush_lesson()
        if current_chapter is not None:
            sections.append(current_chapter)
            current_chapter = None

    def _ensure_lesson() -> IrSection:
        nonlocal current_lesson
        if current_lesson is not None:
            return current_lesson
        assert current_chapter is not None
        sid = f"sec:{prefix}:ch:{_slug(current_chapter.label)}:body"
        current_lesson = IrSection(
            section_id=sid,
            label=f"{current_chapter.label} · {'Overview' if en else '概要'}",
            summary=current_chapter.label,
            text="",
            level=2,
            parent_section_id=current_chapter.section_id,
            start_page=current_chapter.start_page,
            meta={"doc_type": "section", "section_kind": "lesson", "locale_en": en},
        )
        return current_lesson

    sorted_blocks = sorted(blocks, key=lambda b: (b.page_no, b.order))

    for block in sorted_blocks:
        text = (block.text or block.caption or "").strip()
        if block.type == "title" or (block.type == "heading" and is_chapter_text(text)):
            flush_chapter()
            title = normalize_chapter_title(text or block.caption or "章节")
            sid = f"sec:{prefix}:ch:{_slug(title)}"
            current_chapter = IrSection(
                section_id=sid,
                label=title,
                summary=title,
                text="",
                level=1,
                start_page=block.page_no,
                meta={"doc_type": "section", "section_kind": "chapter", "locale_en": en},
            )
            continue
        if block.type == "heading" and is_lesson_text(text):
            flush_lesson()
            title = text
            parent_id = current_chapter.section_id if current_chapter else ""
            sid = f"sec:{prefix}:ls:{_slug(title)}"
            current_lesson = IrSection(
                section_id=sid,
                label=title,
                summary=title,
                text="",
                level=2,
                parent_section_id=parent_id,
                start_page=block.page_no,
                meta={"doc_type": "section", "section_kind": "lesson", "locale_en": en},
            )
            continue

        if current_chapter is None and current_lesson is None:
            orphan_blocks.append(block)
            continue
        if current_lesson is None:
            _ensure_lesson()
        assert current_lesson is not None
        current_lesson.blocks.append(block)

    flush_chapter()

    if orphan_blocks and not sections:
        sid = f"sec:{prefix}:body"
        fallback = IrSection(
            section_id=sid,
            label="Body" if en else "正文",
            summary=textbook_label or ("Body" if en else "正文"),
            text="",
            level=1,
            start_page=orphan_blocks[0].page_no if orphan_blocks else 1,
            blocks=orphan_blocks,
            meta={"doc_type": "section", "section_kind": "body", "locale_en": en},
        )
        finalize_leaf_section(fallback, prefix)
        sections = [fallback]
    elif orphan_blocks:
        preface_blocks, toc_blocks, leftover_blocks = _split_orphan_front_matter(
            orphan_blocks
        )
        front_sections: list[IrSection] = []
        if preface_blocks:
            plabel = _preface_label(preface_blocks, en=en)
            front_sections.append(
                IrSection(
                    section_id=f"sec:{prefix}:preface",
                    label=plabel,
                    summary=plabel,
                    text="",
                    level=1,
                    start_page=preface_blocks[0].page_no,
                    blocks=preface_blocks,
                    meta={
                        "doc_type": "section",
                        "section_kind": "preface",
                        "end_page": preface_blocks[-1].page_no,
                        "locale_en": en,
                    },
                )
            )
        if toc_blocks:
            tlabel = "Contents" if en else "目录"
            front_sections.append(
                IrSection(
                    section_id=f"sec:{prefix}:toc",
                    label=tlabel,
                    summary=tlabel,
                    text="",
                    level=1,
                    start_page=toc_blocks[0].page_no,
                    blocks=toc_blocks,
                    meta={
                        "doc_type": "section",
                        "section_kind": "toc",
                        "end_page": toc_blocks[-1].page_no,
                        "locale_en": en,
                    },
                )
            )
        # No TOC detected — keep legacy single 「前言」 blob
        if not front_sections and orphan_blocks:
            front_sections.append(
                IrSection(
                    section_id=f"sec:{prefix}:front",
                    label="前言" if not en else "Front matter",
                    summary="前言" if not en else "Front matter",
                    text="",
                    level=1,
                    start_page=orphan_blocks[0].page_no,
                    blocks=list(orphan_blocks),
                    meta={"doc_type": "section", "section_kind": "front", "locale_en": en},
                )
            )
            leftover_blocks = []
        for sec in front_sections:
            finalize_leaf_section(sec, prefix)
        # Chapter intro after 目录 (e.g. p8) → first lesson, not under 前言/目录
        if leftover_blocks:
            first_lesson = next(
                (s for s in sections if s.meta.get("section_kind") == "lesson"),
                None,
            )
            if first_lesson is not None:
                first_lesson.blocks = list(leftover_blocks) + list(first_lesson.blocks)
                if leftover_blocks[0].page_no and (
                    not first_lesson.start_page
                    or leftover_blocks[0].page_no < first_lesson.start_page
                ):
                    first_lesson.start_page = leftover_blocks[0].page_no
                finalize_leaf_section(first_lesson, prefix)
            else:
                # No lesson yet — keep as short front leftover under 前言
                if front_sections and front_sections[0].meta.get("section_kind") == "preface":
                    front_sections[0].blocks.extend(leftover_blocks)
                    finalize_leaf_section(front_sections[0], prefix)
                else:
                    plabel = "前言" if not en else "Front matter"
                    intro = IrSection(
                        section_id=f"sec:{prefix}:preface",
                        label=plabel,
                        summary=plabel,
                        text="",
                        level=1,
                        start_page=leftover_blocks[0].page_no,
                        blocks=leftover_blocks,
                        meta={
                            "doc_type": "section",
                            "section_kind": "preface",
                            "locale_en": en,
                        },
                    )
                    finalize_leaf_section(intro, prefix)
                    front_sections.insert(0, intro)
        for i, sec in enumerate(front_sections):
            sections.insert(i, sec)

    default_tb = "Imported textbook" if en else "导入教材"
    tb_label = textbook_label or default_tb
    if not textbook_id:
        textbook_id = f"tb:import:{subject}:{grade_band}:{uuid.uuid4().hex[:8]}"

    textbook = {
        "id": textbook_id,
        "label": tb_label,
        "summary": tb_label[:200],
        "text": tb_label,
        "tags": [f"grade_band:{grade_band}", f"subject:{subject}"],
        "links": {"subject": subject, "grade_band": grade_band},
    }
    return textbook, sections


def _section_kind(sec: IrSection) -> str:
    return str(sec.meta.get("section_kind") or "")


def section_for_page(sections: list[IrSection], page_no: int) -> str | None:
    """Map page number to section by start_page intervals."""
    if not sections:
        return None
    lesson_sections = [
        s
        for s in sections
        if _section_kind(s) in ("lesson", "body", "front", "preface", "toc")
    ]
    pool = sorted(lesson_sections or sections, key=lambda s: s.start_page)
    best = pool[0]
    for sec in pool:
        if sec.start_page <= page_no:
            best = sec
        else:
            break
    return best.section_id
