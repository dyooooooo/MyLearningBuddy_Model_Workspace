"""Unified document Layout IR — engine-agnostic blocks for import / chunking."""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.import_assets_urls import import_asset_studio_url

BlockType = Literal[
    "title",
    "heading",
    "paragraph",
    "list",
    "table",
    "figure",
    "equation",
    "caption",
    "page_break",
]

_HEADING_MD_RE = re.compile(r"^(#{1,4})\s+(.+)$")
# Pattern defaults loaded from Edition seed (data), not textbook-named literals.
from app.services.editions.bundle import SEED_HUJIAO as _SEED_EDITION
from app.services.editions.patterns import patterns_for as _patterns_for

_pat = _patterns_for(_SEED_EDITION)
_CHAPTER_RE = _pat.chapter
_LESSON_RE = _pat.lesson
_FIG_LABEL_RE = _pat.fig_label
_INTERVAL_LABEL_RE = re.compile(
    r"^[\$\(]?\s*[\[\(]?\s*a\s*[,，]\s*b\s*[\]\)]?\s*[\$\)]?\s*\.?$",
    re.IGNORECASE,
)
_FIG_REF_RE = _pat.fig_ref
_OCR_PLACEHOLDER_RE = re.compile(r"^第\s*\d+\s*页原图")
# Numbered example lines: (1) / （1） / mixed half+fullwidth parens
_NUMBERED_LIST_ITEM_RE = re.compile(r"^[（(]\s*([0-9０-９]+)\s*[）)]")
_NUMBERED_LIST_MARKER_RE = re.compile(r"^[（(]\s*([0-9０-９]+)\s*[）)]\s*")
# Find every marker in a glued blob: （3）… (4)…
_NUMBERED_ITEM_FIND_RE = re.compile(r"[（(]\s*[0-9０-９]+\s*[）)]")
_EDGE_WS = " \t\r\n\u3000\xa0\u200b\ufeff"
_SOLUTION_LEAD_RE = _pat.solution_lead


def normalize_block_text(text: str | None) -> str:
    """Strip edge whitespace (incl. fullwidth space) per block and per line."""
    if not text:
        return ""
    lines = [ln.strip(_EDGE_WS) for ln in str(text).replace("\r\n", "\n").split("\n")]
    # Drop empty edge lines; keep internal blank lines collapsed to one
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    out: list[str] = []
    blank = False
    for ln in lines:
        if not ln:
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out)


def _to_ascii_digits(s: str) -> str:
    out: list[str] = []
    for ch in s:
        o = ord(ch)
        if 0xFF10 <= o <= 0xFF19:
            out.append(chr(o - 0xFF10 + ord("0")))
        else:
            out.append(ch)
    return "".join(out)


def is_numbered_list_item(text: str | None) -> bool:
    t = normalize_block_text(text)
    return bool(_NUMBERED_LIST_ITEM_RE.match(t))


def normalize_numbered_list_marker(text: str) -> str:
    """Unify mixed （1）/(1)/ (1） markers to fullwidth （N） for alignment."""
    t = normalize_block_text(text)
    m = _NUMBERED_LIST_MARKER_RE.match(t)
    if not m:
        return t
    n = _to_ascii_digits(m.group(1))
    return f"（{n}）{t[m.end():]}"


def split_multi_numbered_items(text: str | None) -> list[str]:
    """Split a MinerU-glued blob that packs several （1）（2）… into separate items."""
    t = normalize_block_text(text)
    if not t:
        return []
    # OCR often writes list markers as \\left(4\\right)
    t = re.sub(
        r"\\left\s*\(\s*([0-9０-９]+)\s*\\right\s*\)",
        lambda m: f"（{_to_ascii_digits(m.group(1))}）",
        t,
    )
    matches = list(_NUMBERED_ITEM_FIND_RE.finditer(t))
    if len(matches) <= 1:
        return [t]
    chunks: list[str] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(t)
        chunk = normalize_block_text(t[m.start() : end])
        if not chunk:
            continue
        if is_numbered_list_item(chunk):
            chunk = normalize_numbered_list_marker(chunk)
        chunks.append(chunk)
    return chunks or [t]


def split_solution_lead(text: str | None) -> tuple[str, str] | None:
    """If text is a 解 lead, return (\"解\", remainder); else None."""
    t = normalize_block_text(text)
    m = _SOLUTION_LEAD_RE.match(t)
    if not m:
        return None
    rest = normalize_block_text(m.group(1) if m.lastindex else "")
    if rest and is_numbered_list_item(rest):
        rest = normalize_numbered_list_marker(rest)
    return "解", rest


def mark_solution_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Mark 解 lead + following numbered answers in main-column reading order."""
    out: list[DocumentBlock] = []
    in_solution = False
    for block in blocks:
        zone = _layout_zone(block)
        meta = dict(block.meta or {})
        meta.pop("solution_role", None)

        if zone != "main" or block.type in ("figure", "table", "title", "heading"):
            if block.type in ("title", "heading") and zone == "main":
                in_solution = False
            out.append(block.model_copy(update={"meta": meta}))
            continue

        text = normalize_block_text(block.text or "")
        if split_solution_lead(text) is not None:
            meta["solution_role"] = "lead"
            in_solution = True
            out.append(block.model_copy(update={"meta": meta}))
            continue

        if in_solution and is_numbered_list_item(text):
            meta["solution_role"] = "item"
            out.append(block.model_copy(update={"meta": meta}))
            continue

        if in_solution and text:
            in_solution = False
        out.append(block.model_copy(update={"meta": meta}))
    return out


class DocumentBlock(BaseModel):
    id: str
    type: BlockType
    page_no: int = 1
    order: int = 0
    bbox: list[float] | None = None
    text: str | None = None
    table_html: str | None = None
    asset_url: str | None = None
    asset_key: str | None = None
    caption: str | None = None
    level: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def content_length(self) -> int:
        n = len(self.text or "")
        n += len(self.table_html or "")
        n += len(self.caption or "")
        return n


def stable_block_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def bbox_y0(block: dict[str, Any] | DocumentBlock | None) -> float:
    if block is None:
        return 0.0
    bbox = block.bbox if isinstance(block, DocumentBlock) else block.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 2:
        try:
            return float(bbox[1])
        except (TypeError, ValueError):
            pass
    return 0.0


def is_chapter_text(text: str) -> bool:
    s = text.strip()
    m = _HEADING_MD_RE.match(s)
    if m:
        return bool(_CHAPTER_RE.match(m.group(2).strip()))
    return bool(_CHAPTER_RE.match(s))


def is_lesson_text(text: str) -> bool:
    s = text.strip()
    m = _HEADING_MD_RE.match(s)
    if m:
        return bool(_LESSON_RE.match(m.group(2).strip()))
    return bool(_LESSON_RE.match(s))


def heading_level_from_text(text: str) -> int | None:
    m = _HEADING_MD_RE.match(text.strip())
    if m:
        return len(m.group(1))
    if is_chapter_text(text):
        return 1
    if is_lesson_text(text):
        return 2
    return None


def asset_url_from_key(asset_key: str | None) -> str | None:
    if not asset_key:
        return None
    return import_asset_studio_url(asset_key)


def normalize_chapter_title(text: str) -> str:
    """Use first chapter line only for true chapter titles.

    VLM TOC blobs that start with ``第N章`` must be classified as TOC in
    ``mineru_adapter`` *before* this runs; otherwise the outline is destroyed.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        plain = _HEADING_MD_RE.sub(r"\2", line) if _HEADING_MD_RE.match(line) else line
        if is_chapter_text(plain):
            return plain.strip()
    first = text.splitlines()[0].strip() if text else ""
    return _HEADING_MD_RE.sub(r"\2", first) if _HEADING_MD_RE.match(first) else first


def normalize_figure_caption(caption: str | None) -> str | None:
    """Drop MinerU axis-interval OCR noise; keep 图 X-X-X labels."""
    if not caption or not caption.strip():
        return None
    lines = [ln.strip() for ln in caption.splitlines() if ln.strip()]
    fig_lines = [ln for ln in lines if _FIG_LABEL_RE.match(ln)]
    if fig_lines:
        return fig_lines[-1]
    if lines and all(_INTERVAL_LABEL_RE.match(ln) or _is_interval_notation(ln) for ln in lines):
        return None
    if len(lines) == 1 and _is_interval_notation(lines[0]):
        return None
    return caption.strip()


def _is_interval_notation(text: str) -> bool:
    s = text.strip().replace(" ", "")
    if _INTERVAL_LABEL_RE.match(text.strip()):
        return True
    if s.startswith("$") and s.endswith("$") and "a" in s and "b" in s:
        return True
    if re.match(r"^[\(\[]\s*a\s*[,，]\s*b\s*[\)\]]\.?$", s):
        return True
    return False


def clean_vision_ocr_text(text: str) -> str:
    """Strip fences and figure-placeholder lines from Vision OCR output."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:markdown)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if _OCR_PLACEHOLDER_RE.match(stripped):
            continue
        if "页原图/插图" in stripped:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def figure_reference_count(text: str) -> int:
    return len(_FIG_REF_RE.findall(text or ""))


def render_block_markdown(block: DocumentBlock) -> str:
    if block.type == "figure" and block.asset_url:
        cap = normalize_figure_caption(block.caption)
        alt = cap or "插图"
        parts = [f"![{alt}]({block.asset_url})"]
        if cap:
            parts.append(f"*{cap}*")
        return "\n\n".join(parts)

    parts: list[str] = []
    if block.caption and block.type in ("table", "equation"):
        parts.append(block.caption)
    if block.type in ("heading", "title") and block.text:
        level = block.level or 2
        prefix = "#" * max(1, min(4, level))
        title_text = block.text.strip()
        if block.type == "title":
            title_text = normalize_chapter_title(title_text)
        parts.append(f"{prefix} {title_text}")
    elif block.type == "table":
        if block.table_html:
            parts.append(block.table_html.strip())
        elif block.text:
            parts.append(block.text.strip())
    elif block.text:
        parts.append(block.text.strip())
    elif block.type == "equation" and block.text:
        parts.append(block.text.strip())
    if block.asset_url and block.type != "figure":
        alt = normalize_figure_caption(block.caption) or {
            "equation": "公式",
            "table": "表格",
        }.get(block.type, "图")
        parts.append(f"![{alt}]({block.asset_url})")
    return "\n\n".join(p for p in parts if p.strip())


def blocks_for_page(blocks: list[DocumentBlock], page_no: int) -> list[DocumentBlock]:
    """Blocks of one page in reading order.

    ``order`` is the authority — that is the field page edits and reorder writes
    touch. The stored array can be out of step with it (a page-level write appends
    its blocks at the end, and some older jobs hold a scrambled array), so sort
    here instead of trusting storage. Ties keep their stored position.
    """
    page = [b for b in blocks if b.page_no == page_no]
    return sorted(page, key=lambda b: b.order)


def render_page_markdown(blocks: list[DocumentBlock], page_no: int) -> str:
    page_blocks = blocks_for_page(blocks, page_no)
    parts = [render_block_markdown(b) for b in page_blocks]
    return "\n\n".join(p for p in parts if p.strip())


def render_document_markdown(blocks: list[DocumentBlock]) -> str:
    pages = sorted({b.page_no for b in blocks})
    parts: list[str] = []
    for page_no in pages:
        page_md = render_page_markdown(blocks, page_no)
        if page_md.strip():
            parts.append(page_md)
    return "\n\n---\n\n".join(parts)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_CHAPTER_TOC_RE = _pat.chapter_toc


def _print_page_to_pdf_map(blocks: list[DocumentBlock]) -> dict[int, int]:
    """Map printed page numbers (书页码) → PDF page_no from running page_number blocks."""
    out: dict[int, int] = {}
    for b in blocks:
        role = str((b.meta or {}).get("pedagogy_role") or "")
        mtype = str((b.meta or {}).get("mineru_type") or "")
        if role != "page_number" and mtype != "page_number":
            continue
        t = (b.text or "").strip()
        if t.isdigit():
            out[int(t)] = b.page_no
    return out


def _toc_level(label: str, *, is_chapter: bool) -> int:
    """0=章, 1=节/提要/复习题, 2=更细."""
    if is_chapter:
        return 0
    s = (label or "").strip()
    if re.match(r"^\d+\.\d+\.\d+\b", s):
        return 2
    if re.match(r"^\d+\.\d+\b", s):
        return 1
    if s.startswith(("内容提要", "复习题")):
        return 1
    return 1


def _render_toc_html(
    block: DocumentBlock,
    *,
    print_to_pdf: dict[int, int] | None = None,
) -> str:
    """Render TOC: title | dotted leader | right-aligned page; whole row clickable."""

    def _row_html(
        *,
        label: str,
        print_page: int | None,
        is_chapter: bool,
        level: int | None = None,
    ) -> str:
        label = (label or "").strip()
        if not label and print_page is None:
            return ""
        if not is_chapter and label:
            is_chapter = bool(_CHAPTER_TOC_RE.match(label))
        lv = 0 if is_chapter else (
            level if level is not None else _toc_level(label, is_chapter=False)
        )
        classes = ["lb-toc-row", f"lb-toc-row--lv{lv}"]
        if is_chapter:
            classes.append("lb-toc-row--chapter")
        attrs: list[str] = []
        leader = ""
        page_html = ""
        if print_page is not None:
            try:
                pp = int(print_page)
            except (TypeError, ValueError):
                pp = None
            if pp is not None:
                classes.append("lb-toc-row--link")
                attrs.append(f'data-goto-print-page="{pp}"')
                pdf_page = (print_to_pdf or {}).get(pp)
                if pdf_page:
                    attrs.append(f'data-goto-page="{int(pdf_page)}"')
                attrs.append('role="link"')
                attrs.append('tabindex="0"')
                # Explicit leader BETWEEN title and page (not ::after after page).
                leader = '<span class="lb-toc-leader" aria-hidden="true"></span>'
                page_html = f'<span class="lb-toc-page">{pp}</span>'
        row_cls = " ".join(classes)
        attr_s = (" " + " ".join(attrs)) if attrs else ""
        return (
            f'<li class="{row_cls}"{attr_s}>'
            f'<span class="lb-toc-title">{_escape_html(label)}</span>'
            f"{leader}{page_html}</li>"
        )

    items: list[str] = []
    entries = (block.meta or {}).get("toc_entries")
    if isinstance(entries, list) and entries:
        for ent in entries:
            if not isinstance(ent, dict):
                continue
            label = str(ent.get("label") or "")
            is_chapter = bool(ent.get("is_chapter"))
            lv = ent.get("level")
            try:
                lv_i = int(lv) if lv is not None else None
            except (TypeError, ValueError):
                lv_i = None
            row = _row_html(
                label=label,
                print_page=ent.get("print_page"),  # type: ignore[arg-type]
                is_chapter=is_chapter,
                level=lv_i,
            )
            if row:
                items.append(row)
    elif block.text:
        for line in block.text.splitlines():
            line = line.strip().lstrip("-•· ").strip()
            if not line:
                continue
            norm = line.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
            m = re.match(
                r"^(?P<label>.+?)(?:\s*[\.．…·•]+)*\s+(?P<page>\d{1,4})\s*$",
                norm,
            )
            if (
                m
                and m.group("label").strip(" .\u3000．…·•")
                and not m.group("label").strip().isdigit()
            ):
                label = m.group("label").strip(" .\u3000．…·•")
                pp = int(m.group("page"))
                is_ch = bool(_CHAPTER_TOC_RE.match(label))
                row = _row_html(label=label, print_page=pp, is_chapter=is_ch)
            else:
                is_ch = bool(_CHAPTER_TOC_RE.match(norm))
                row = _row_html(label=norm, print_page=None, is_chapter=is_ch)
            if row:
                items.append(row)
    if not items:
        return ""
    bid = f' data-block-id="{_escape_html(block.id)}"'
    return f'<nav class="lb-toc-wrap"{bid}><ol class="lb-toc">{"".join(items)}</ol></nav>'


def _callout_class(role: str) -> str:
    if role in ("think",):
        return "lb-callout lb-callout--think"
    if role in ("prompt",):
        return "lb-callout lb-callout--prompt"
    if role in ("note", "symbol_note", "annotation"):
        return "lb-callout lb-callout--note"
    return "lb-callout"


def _layout_zone(block: DocumentBlock) -> str:
    return str((block.meta or {}).get("layout_zone") or "main")


def _pedagogy_role(block: DocumentBlock) -> str:
    return str((block.meta or {}).get("pedagogy_role") or "body")


def _should_skip_render(block: DocumentBlock) -> bool:
    meta = block.meta or {}
    if meta.get("skip_render"):
        return True
    if _layout_zone(block) == "running":
        return True
    return False


_INLINE_MATH_RE = re.compile(r"\$([^$\n]+?)\$")
_DISPLAY_MATH_RE = re.compile(r"\$\$([\s\S]+?)\$\$")
_BARE_LATEX_RE = re.compile(r"\\(?:[a-zA-Z]+|[{}\[\]])")
_CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def _render_math_span(latex: str, *, display: bool = False) -> str:
    inner = latex.strip()
    if display:
        return (
            f'<span class="math math--display" data-latex="{_escape_html(inner)}"></span>'
        )
    return f'<span class="math math--inline" data-latex="{_escape_html(inner)}"></span>'


def _render_math_fallback(raw: str) -> str:
    """Invalid math: keep raw Unicode visible (not typeset)."""
    return f'<span class="math math--fallback">{_escape_html(raw.strip())}</span>'


def _render_segments_html(segments: list[dict[str, Any]]) -> str:
    """Render normalize_plain segments: valid math -> MathJax, else raw text."""
    parts: list[str] = []
    for seg in segments:
        raw = str(seg.get("raw") or "")
        if seg.get("kind") == "math":
            latex = seg.get("latex")
            if seg.get("valid") and latex:
                parts.append(_render_math_span(str(latex), display=False))
            else:
                parts.append(_render_math_fallback(raw))
        else:
            parts.append(_escape_html(raw))
    return "".join(parts)


def _looks_like_bare_latex(text: str) -> bool:
    """True only for math-only snippets — never wrap list/prose as one formula."""
    t = text.strip()
    if not t or "$" in t:
        return False
    # Numbered list structure must stay as list + inline math, not one span.
    if _NUMBERED_LIST_MARKER_RE.match(t) or len(_NUMBERED_ITEM_FIND_RE.findall(t)) >= 2:
        return False
    # Mixed CJK + ``\leqslant`` etc. must be split, not one math span.
    if len(_CJK_CHAR_RE.findall(t)) >= 2:
        return False
    if _BARE_LATEX_RE.search(t):
        return True
    return bool(re.search(r"[αβγθπ∞∈∉∪∩≤≥≠]", t) and re.search(r"[=<>+\-*/(){}|]", t))


def _needs_math_segmentation(text: str) -> bool:
    """Bare LaTeX coexists with list markers or CJK → run normalize_plain."""
    has_tex = bool(_BARE_LATEX_RE.search(text) or re.search(r"[αβγθπ∞∈∉∪∩≤≥≠]", text))
    if not has_tex:
        return False
    if len(_CJK_CHAR_RE.findall(text)) >= 1:
        return True
    if _NUMBERED_LIST_MARKER_RE.match(text.strip()):
        return True
    if len(_NUMBERED_ITEM_FIND_RE.findall(text)) >= 2:
        return True
    return False


def _render_via_normalize_plain(text: str) -> str | None:
    """Split prose/list markers from bare LaTeX via normalize_plain; None if unavailable."""
    try:
        from app.math.normalize import normalize_plain
    except ImportError:
        return None
    rich = normalize_plain(text)
    segs = [
        {
            "kind": seg.kind,
            "raw": seg.raw,
            "latex": seg.latex,
            "valid": bool(seg.valid),
        }
        for seg in rich.segments
    ]
    if not any(s.get("kind") == "math" and s.get("valid") for s in segs):
        return None
    return _render_segments_html(segs)


def _render_text_with_math(text: str) -> str:
    """Escape plain text but preserve $...$ / $$...$$ / mixed bare LaTeX as MathJax."""
    if _needs_math_segmentation(text):
        segmented = _render_via_normalize_plain(text)
        if segmented is not None:
            return segmented

    if _looks_like_bare_latex(text):
        display = "\n" in text or len(text) > 80
        return _render_math_span(text, display=display)

    parts: list[str] = []
    cursor = 0
    for m in _DISPLAY_MATH_RE.finditer(text):
        if m.start() > cursor:
            parts.append(_escape_html(text[cursor : m.start()]))
        parts.append(_render_math_span(m.group(1), display=True))
        cursor = m.end()
    tail = text[cursor:]
    cursor = 0
    while cursor < len(tail):
        m = _INLINE_MATH_RE.search(tail, cursor)
        if not m:
            parts.append(_escape_html(tail[cursor:]))
            break
        if m.start() > cursor:
            parts.append(_escape_html(tail[cursor : m.start()]))
        parts.append(_render_math_span(m.group(1), display=False))
        cursor = m.end()
    return "".join(parts)


def _render_paragraph_html(text: str, *, css_class: str | None = None) -> str:
    raw = normalize_block_text(text)
    chunks = split_multi_numbered_items(raw)
    if len(chunks) > 1:
        return "".join(_render_paragraph_html(c, css_class=css_class) for c in chunks)
    if is_numbered_list_item(raw):
        raw = normalize_numbered_list_marker(raw)
        cls = css_class or "lb-list-item"
        return f'<p class="{cls}">{_render_text_with_math(raw)}</p>'
    if css_class:
        return f'<p class="{css_class}">{_render_text_with_math(raw)}</p>'
    return f"<p>{_render_text_with_math(raw)}</p>"


def _render_solution_lead_html(text: str) -> str:
    """解 shares question indent; first answer in tail; further （N） as solution-items."""
    split = split_solution_lead(text)
    if split is None:
        return _render_paragraph_html(text)
    _label, rest = split
    chunks = split_multi_numbered_items(rest) if rest else []
    if rest and not chunks:
        chunks = [rest]
    first = chunks[0] if chunks else ""
    tail = ""
    if first:
        tail = f'<span class="lb-solution-tail">{_render_text_with_math(first)}</span>'
    parts = [
        f'<p class="lb-list-item lb-solution-lead">'
        f'<span class="lb-solution-label">解</span>{tail}</p>'
    ]
    for chunk in chunks[1:]:
        parts.append(_render_paragraph_html(chunk, css_class="lb-solution-item"))
    return "".join(parts)


def _render_paragraph_segments_html(
    segments: list[dict[str, Any]],
    *,
    css_class: str | None = None,
) -> str:
    # Reconstruct plain prefix for list detection; keep segment HTML body.
    plain_parts: list[str] = []
    for seg in segments:
        if seg.get("kind") == "text":
            plain_parts.append(str(seg.get("text") or seg.get("raw") or ""))
        elif seg.get("kind") == "math":
            plain_parts.append(str(seg.get("latex") or ""))
    plain = normalize_block_text("".join(plain_parts))
    body = _render_segments_html(segments)
    if is_numbered_list_item(plain):
        # Normalize leading marker in the first text segment when present.
        fixed: list[dict[str, Any]] = []
        marker_done = False
        for seg in segments:
            if not marker_done and seg.get("kind") == "text":
                t = normalize_block_text(str(seg.get("text") or seg.get("raw") or ""))
                if is_numbered_list_item(t) or _NUMBERED_LIST_MARKER_RE.match(t):
                    t = normalize_numbered_list_marker(t)
                    key = "text" if "text" in seg else "raw"
                    fixed.append({**seg, key: t})
                    marker_done = True
                    continue
            fixed.append(seg)
        body = _render_segments_html(fixed if marker_done else segments)
        cls = css_class or "lb-list-item"
        return f'<p class="{cls}">{body}</p>'
    if css_class:
        return f'<p class="{css_class}">{body}</p>'
    return f"<p>{body}</p>"


def _equation_segments_all_valid(segments: list[dict[str, Any]]) -> bool:
    math_segs = [s for s in segments if s.get("kind") == "math"]
    return bool(math_segs) and all(s.get("valid") and s.get("latex") for s in math_segs)


def _render_equation_html(text: str) -> str:
    raw = text.strip()
    if raw.startswith("$$") and raw.endswith("$$"):
        inner = raw[2:-2].strip()
    elif raw.startswith("$") and raw.endswith("$"):
        inner = raw[1:-1].strip()
    else:
        inner = raw
    return (
        f'<div class="lb-equation">'
        f'<span class="math math--display" data-latex="{_escape_html(inner)}"></span>'
        f"</div>"
    )


def _render_equation_block_html(block: DocumentBlock) -> str:
    """Equation HTML from ``text``, or from segments when the latex is mixed.

    ``enrich_block_math`` folds fully validated latex back into ``block.text``,
    so all-valid segments carry nothing extra and ``text`` wins. Segments are
    only consulted when some math failed to validate — there they keep the
    invalid fragments visible as raw Unicode instead of one broken formula.
    """
    segments = (block.meta or {}).get("text_segments")
    text = (block.text or "").strip()
    if segments and not _equation_segments_all_valid(segments):
        return f'<div class="lb-equation">{_render_segments_html(segments)}</div>'
    return _render_equation_html(text)


@dataclass(frozen=True)
class BlockRenderContext:
    """Everything a block renderer is allowed to look at."""

    block: DocumentBlock
    role: str
    zone: str
    bid_attr: str
    anchor_attr: str
    print_to_pdf: dict[int, int] | None = None


# Two registries, both ordered by descending priority:
#   standalone → emits a finished fragment (its own <figure>, TOC list, ...)
#   body       → emits inner HTML that the shared wrapper puts in a container
# New block kinds plug in here instead of growing the if/elif chain, and the
# wrapper below stays the single place that decides callout vs lb-block.
_StandaloneRenderer = Callable[[BlockRenderContext], str | None]
_BodyRenderer = Callable[[BlockRenderContext], list[str]]

_STANDALONE_RENDERERS: list[tuple[int, str, _StandaloneRenderer]] = []
_BODY_RENDERERS: list[tuple[int, str, _BodyRenderer]] = []


def register_standalone_renderer(
    name: str, fn: _StandaloneRenderer, *, priority: int = 0
) -> None:
    """Register a renderer that returns a complete HTML fragment (or None)."""
    _STANDALONE_RENDERERS.append((priority, name, fn))
    _STANDALONE_RENDERERS.sort(key=lambda item: -item[0])


def register_body_renderer(name: str, fn: _BodyRenderer, *, priority: int = 0) -> None:
    """Register a renderer producing inner HTML for the shared block wrapper."""
    _BODY_RENDERERS.append((priority, name, fn))
    _BODY_RENDERERS.sort(key=lambda item: -item[0])


def _render_toc_standalone(ctx: BlockRenderContext) -> str | None:
    meta = ctx.block.meta or {}
    if not (
        ctx.role == "toc"
        or meta.get("mineru_type") == "index"
        or meta.get("toc_entries")
    ):
        return None
    return _render_toc_html(ctx.block, print_to_pdf=ctx.print_to_pdf) or None


def _render_figure_standalone(ctx: BlockRenderContext) -> str | None:
    block = ctx.block
    if not (block.type == "figure" and block.asset_url):
        return None
    cap = normalize_figure_caption(block.caption)
    alt = _escape_html(cap or "插图")
    img = (
        f'<div class="lb-fig-cell">'
        f'<img src="{_escape_html(block.asset_url)}" alt="{alt}" />'
        f"</div>"
    )
    if cap:
        return (
            f'<figure class="lb-fig-item"{ctx.bid_attr}><div>{img}</div>'
            f"<figcaption>{_escape_html(cap)}</figcaption></figure>"
        )
    return f'<figure class="lb-fig-item"{ctx.bid_attr}>{img}</figure>'


def _render_heading_body(ctx: BlockRenderContext) -> list[str]:
    block = ctx.block
    if block.type not in ("heading", "title") or not block.text:
        return []
    level = min(4, max(1, block.level or 2))
    title_text = block.text.strip()
    if block.type == "title":
        title_text = normalize_chapter_title(title_text)
    return [f"<h{level}>{_escape_html(title_text)}</h{level}>"]


def _render_table_body(ctx: BlockRenderContext) -> list[str]:
    block = ctx.block
    if block.type != "table":
        return []
    if block.table_html and block.table_html.strip().startswith("<"):
        return [f'<div class="lb-table-wrap">{block.table_html.strip()}</div>']
    if block.text:
        return [_render_paragraph_html(block.text)]
    # A table block with neither HTML nor text still owns the block: stop here
    # rather than falling through to the paragraph renderer.
    return [""]


def _render_equation_body(ctx: BlockRenderContext) -> list[str]:
    block = ctx.block
    if block.type != "equation" or not block.text:
        return []
    return [_render_equation_block_html(block)]


def _render_text_body(ctx: BlockRenderContext) -> list[str]:
    block = ctx.block
    if not block.text:
        return []
    solution_role = str((block.meta or {}).get("solution_role") or "")
    if solution_role == "lead":
        return [_render_solution_lead_html(block.text)]

    item_cls = "lb-solution-item" if solution_role == "item" else None
    # Glued （3）…(4)… blobs: always split on markers (ignore stale segments).
    multi = split_multi_numbered_items(block.text)
    if len(multi) > 1:
        return [_render_paragraph_html(chunk, css_class=item_cls) for chunk in multi]

    segments = (block.meta or {}).get("text_segments")
    if segments and solution_role != "item":
        return [_render_paragraph_segments_html(segments)]
    if segments and solution_role == "item":
        return [_render_paragraph_segments_html(segments, css_class=item_cls)]

    parts: list[str] = []
    for para in normalize_block_text(block.text).split("\n\n"):
        p = normalize_block_text(para)
        if p:
            parts.append(_render_paragraph_html(p, css_class=item_cls))
    return parts


register_standalone_renderer("toc", _render_toc_standalone, priority=100)
register_standalone_renderer("figure", _render_figure_standalone, priority=90)
register_body_renderer("heading", _render_heading_body, priority=100)
register_body_renderer("table", _render_table_body, priority=90)
register_body_renderer("equation", _render_equation_body, priority=80)
register_body_renderer("text", _render_text_body, priority=10)


def _wrap_block_html(ctx: BlockRenderContext, body: str) -> str:
    """Single decision point for callout vs plain block container."""
    role, zone = ctx.role, ctx.zone
    if zone in ("aside_right", "aside_left") and role not in ("body", "running_header"):
        label = {
            "think": "思考",
            "note": "注释",
            "prompt": "提示",
            "symbol_note": "注释",
        }.get(role, "")
        label_html = f'<span class="lb-callout-label">{label}</span>' if label else ""
        return (
            f'<div class="{_callout_class(role)}"{ctx.bid_attr}{ctx.anchor_attr}>'
            f"{label_html}{body}</div>"
        )
    cls = "lb-block lb-full-bleed" if zone == "full_bleed" else "lb-block"
    return f'<div class="{cls}"{ctx.bid_attr}{ctx.anchor_attr}>{body}</div>'


def render_block_html(
    block: DocumentBlock,
    *,
    print_to_pdf: dict[int, int] | None = None,
) -> str:
    if _should_skip_render(block):
        return ""

    anchor = (block.meta or {}).get("anchor_block_id") or ""
    ctx = BlockRenderContext(
        block=block,
        role=_pedagogy_role(block),
        zone=_layout_zone(block),
        bid_attr=f' data-block-id="{_escape_html(block.id)}"',
        anchor_attr=(
            f' data-anchor="{_escape_html(str(anchor))}"' if anchor else ""
        ),
        print_to_pdf=print_to_pdf,
    )

    for _priority, _name, renderer in _STANDALONE_RENDERERS:
        fragment = renderer(ctx)
        if fragment:
            return fragment

    inner_parts: list[str] = []
    for _priority, _name, body_renderer in _BODY_RENDERERS:
        parts = body_renderer(ctx)
        if parts:
            inner_parts = parts
            break

    if not any(p.strip() for p in inner_parts) and block.asset_url:
        alt = normalize_figure_caption(block.caption) or "图"
        inner_parts = [
            f'<img src="{_escape_html(block.asset_url)}" alt="{_escape_html(alt)}" />'
        ]

    body = "".join(inner_parts)
    if not body.strip():
        return ""
    return _wrap_block_html(ctx, body)


def _group_figure_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock | list[DocumentBlock]]:
    """Merge adjacent blocks sharing figure_group_id."""
    out: list[DocumentBlock | list[DocumentBlock]] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        gid = (b.meta or {}).get("figure_group_id")
        if b.type == "figure" and gid:
            group = [b]
            j = i + 1
            while j < len(blocks):
                nb = blocks[j]
                if nb.type == "figure" and (nb.meta or {}).get("figure_group_id") == gid:
                    group.append(nb)
                    j += 1
                else:
                    break
            if len(group) > 1:
                out.append(group)
                i = j
                continue
        out.append(b)
        i += 1
    return out


def _figure_axis(block: DocumentBlock, idx: int, default: float = 0.0) -> float:
    bb = block.bbox or []
    if len(bb) > idx:
        return float(bb[idx])
    return default


def _cluster_figure_rows(
    group: list[DocumentBlock], *, y_tol: float = 40.0
) -> list[list[DocumentBlock]]:
    """Split a figure group into visual rows by bbox y0 (for 2×2 grids)."""
    ordered = sorted(
        group,
        key=lambda b: (
            _figure_axis(b, 1, float(b.order)),
            _figure_axis(b, 0, float(b.order)),
            b.order,
        ),
    )
    rows: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    row_y: float | None = None
    for fig in ordered:
        y0 = _figure_axis(fig, 1, float(fig.order))
        if current and row_y is not None and abs(y0 - row_y) > y_tol:
            rows.append(current)
            current = [fig]
            row_y = y0
        else:
            if row_y is None:
                row_y = y0
            current.append(fig)
    if current:
        rows.append(current)
    return rows


def _render_figure_group_html(group: list[DocumentBlock]) -> str:
    """Render multi-panel figures: per-cell labels + optional shared 图 X-X caption.

    Prefer meta.figure_group_caption / a 图-label caption as the group figcaption.
    Do **not** promote the last panel's sub-label (e.g. ``cos α``) to the title.
    """
    group_caption = ""
    for fig in group:
        meta_cap = (fig.meta or {}).get("figure_group_caption")
        if meta_cap and str(meta_cap).strip():
            group_caption = str(meta_cap).strip()
            break
    if not group_caption:
        for fig in group:
            cap = normalize_figure_caption(fig.caption)
            if cap and _FIG_LABEL_RE.match(cap):
                group_caption = cap
                break

    rows_html: list[str] = []
    for row in _cluster_figure_rows(group):
        cells: list[str] = []
        for fig in row:
            if not fig.asset_url:
                continue
            cell_cap = normalize_figure_caption(fig.caption)
            if cell_cap and (
                (group_caption and cell_cap == group_caption)
                or _FIG_LABEL_RE.match(cell_cap)
            ):
                if not group_caption and _FIG_LABEL_RE.match(cell_cap):
                    group_caption = cell_cap
                cell_cap = None
            alt = _escape_html(cell_cap or group_caption or "插图")
            inner = f'<img src="{_escape_html(fig.asset_url)}" alt="{alt}" />'
            if cell_cap:
                inner += f'<div class="lb-fig-cell-cap">{_escape_html(cell_cap)}</div>'
            cells.append(f'<div class="lb-fig-cell">{inner}</div>')
        if cells:
            rows_html.append(f'<div class="lb-fig-row">{"".join(cells)}</div>')

    if not group_caption:
        caps = [normalize_figure_caption(f.caption) for f in group]
        uniq = {c for c in caps if c}
        if len(uniq) == 1:
            group_caption = next(iter(uniq))

    gid = (group[0].meta or {}).get("figure_group_id") or group[0].id
    cap_html = (
        f"<figcaption>{_escape_html(group_caption)}</figcaption>"
        if group_caption
        else ""
    )
    return (
        f'<figure class="lb-fig-group" data-figure-group="{_escape_html(str(gid))}">'
        f'{"".join(rows_html)}{cap_html}</figure>'
    )


def _zone_container_class(zone: str) -> str:
    if zone == "aside_left":
        return "lb-aside lb-aside--left"
    if zone == "aside_right":
        return "lb-aside lb-aside--right"
    if zone == "full_bleed":
        return "lb-full-bleed"
    return "lb-main"


def _wrap_aside_item(html: str, gap_em: float) -> str:
    """Place aside fragment with normal-flow top margin (PDF-relative gap)."""
    gap = max(0.0, min(48.0, float(gap_em)))
    return (
        f'<div class="lb-aside-item" style="--lb-gap:{gap:.2f}" '
        f'data-lb-gap="{gap:.2f}">{html}</div>'
    )


def _bbox_y0_y1(block: DocumentBlock) -> tuple[float, float]:
    bb = block.bbox or []
    if len(bb) >= 4:
        y0, y1 = float(bb[1]), float(bb[3])
        if y1 < y0:
            y0, y1 = y1, y0
        return y0, y1
    if len(bb) >= 2:
        y0 = float(bb[1])
        return y0, y0 + 40.0
    return 0.0, 40.0


def _first_toc_page_no(blocks: list[DocumentBlock]) -> int | None:
    """PDF page_no of the first TOC / index block, if any."""
    best: int | None = None
    for b in blocks:
        if (b.meta or {}).get("pedagogy_role") != "toc":
            continue
        pn = int(b.page_no or 0)
        if pn <= 0:
            continue
        if best is None or pn < best:
            best = pn
    return best


def render_zone_grid_html(
    *,
    page_no: int,
    profile_id: str,
    header: str = "",
    by_zone: dict[str, list[str]],
    page_number: str = "",
    footer: str = "",
    toc_page_no: int | None = None,
    header_parts: list[str] | list[tuple[str, str]] | None = None,
    page_number_slot: str = "aside_right",
    page_number_block_id: str = "",
    footer_block_id: str = "",
) -> str:
    """Single page skeleton shared by every renderer (IR + 35B blueprint).

    ``by_zone`` maps zone -> list of already-rendered block HTML fragments.
    Keeping this the *only* place that emits ``lb-page`` / ``lb-grid`` / zone
    containers prevents structural drift between the two rendering paths.

    Empty aside columns are omitted so main stays beside real sidebars (not
    stacked under a phantom left column). Grid class encodes which columns
    are present: ``lb-grid--m`` / ``lb-grid--l`` / ``lb-grid--r`` / ``lb-grid--lr``.

    When ``toc_page_no`` is set, the footer page number becomes a link back to
    the table-of-contents page (hover title: 返回目录页).

    ``header_parts`` (preferred) renders multiple running headers; ``header``
    remains as a single-string fallback for callers that only have one title.
    Each part may be a bare string or ``(block_id, text)`` for clickable chrome.
    """
    raw_parts = list(header_parts or [])
    parts_hdr: list[str | tuple[str, str]] = [
        p for p in raw_parts if (p[1] if isinstance(p, tuple) else p)
    ]
    if not parts_hdr and header and str(header).strip():
        parts_hdr = [str(header).strip()]

    pn_text = str(page_number or "").strip()
    print_attr = ""
    if pn_text.isdigit():
        print_attr = f' data-print-page="{_escape_html(pn_text)}"'
    parts = [
        f'<article class="lb-page" data-page="{page_no}"{print_attr} '
        f'data-profile="{_escape_html(profile_id)}">',
    ]
    hdr_html = _render_running_header_html(parts_hdr)
    if hdr_html:
        parts.append(hdr_html)

    def _zone_has_content(zone: str) -> bool:
        chunks = by_zone.get(zone) or []
        if not chunks:
            return False
        joined = "".join(chunks).strip()
        if not joined:
            return False
        if 'lb-col-placeholder' in joined and "lb-aside-item" not in joined and "lb-block" not in joined:
            return False
        return True

    has_left = _zone_has_content("aside_left")
    has_right = _zone_has_content("aside_right")
    if has_left and has_right:
        grid_mod = "lb-grid--lr"
    elif has_left:
        grid_mod = "lb-grid--l"
    elif has_right:
        grid_mod = "lb-grid--r"
    else:
        grid_mod = "lb-grid--m"

    parts.append(f'<div class="lb-grid {grid_mod}">')

    zones: list[str] = []
    if has_left:
        zones.append("aside_left")
    zones.append("main")
    if has_right:
        zones.append("aside_right")

    for zone in zones:
        chunks = by_zone.get(zone) or []
        cls = _zone_container_class(zone)
        tag = "aside" if zone.startswith("aside") else "section"
        inner = "".join(chunks)
        if not inner and zone == "main":
            inner = ""
        parts.append(f'<{tag} class="{cls}" data-zone="{zone}">{inner}</{tag}>')

    parts.append("</div>")

    full_bleed = by_zone.get("full_bleed") or []
    if full_bleed:
        parts.append(
            f'<div class="lb-full-bleed" data-zone="full_bleed">{"".join(full_bleed)}</div>'
        )

    foot_bits: list[str] = []
    ft = str(footer or "").strip()
    if ft:
        ft_bid = f' data-block-id="{_escape_html(footer_block_id)}"' if footer_block_id else ""
        foot_bits.append(
            f'<span class="lb-running-ft lb-chrome-hit"{ft_bid}>{_escape_html(ft)}</span>'
        )
    pn_slot = page_number_slot if page_number_slot in ("aside_left", "aside_right", "main") else "aside_right"
    if pn_text:
        pn = _escape_html(pn_text)
        side_cls = "lb-page-number--left" if pn_slot == "aside_left" else "lb-page-number--right"
        pn_bid = (
            f' data-block-id="{_escape_html(page_number_block_id)}"'
            if page_number_block_id
            else ""
        )
        if toc_page_no and int(toc_page_no) > 0 and int(toc_page_no) != int(page_no):
            pn_html = (
                f'<span class="lb-page-number lb-page-number--toc-link lb-chrome-hit {side_cls}" '
                f'title="返回目录页" role="link" tabindex="0" '
                f'data-goto-page="{int(toc_page_no)}"{pn_bid}>{pn}</span>'
            )
        else:
            pn_html = (
                f'<span class="lb-page-number lb-chrome-hit {side_cls}"{pn_bid}>{pn}</span>'
            )
        if pn_slot == "aside_left":
            foot_bits.insert(0, pn_html)
        else:
            foot_bits.append(pn_html)
    if foot_bits:
        ft_cls = "lb-running-ft-row"
        if pn_slot == "aside_left":
            ft_cls += " lb-running-ft-row--pn-left"
        parts.append(f'<footer class="{ft_cls}">{"".join(foot_bits)}</footer>')

    parts.append("</article>")
    return sanitize_page_html("\n".join(parts))


def _pick_running_chrome(
    running: list[DocumentBlock],
) -> tuple[list[tuple[str, str]], tuple[str, str], tuple[str, str], str]:
    """Split running-zone blocks into header parts / page_number / footer.

    Multiple ``page_header`` blocks on one page (e.g. chapter no. + section
    title) are all kept, ordered left-to-right then top-to-bottom.

    Returns
    ``(header_parts, page_number, footer, page_number_slot)`` where each of
    header_parts / page_number / footer is ``(block_id, text)`` (empty id/text
    when absent), and ``page_number_slot`` is
    ``aside_left`` | ``main`` | ``aside_right``.
    """
    header_items: list[tuple[float, float, str, str]] = []
    page_number = ("", "")
    page_number_slot = "aside_right"
    footer = ("", "")
    for rb in running:
        t = (rb.text or "").strip()
        if not t:
            continue
        role = _pedagogy_role(rb)
        mineru_type = str((rb.meta or {}).get("mineru_type") or "")
        bbox = rb.bbox or [0.0, 500.0, 0.0, 0.0]
        x0 = float(bbox[0]) if len(bbox) > 0 else 0.0
        y0 = float(bbox[1]) if len(bbox) > 1 else 500.0
        slot = str((rb.meta or {}).get("chrome_slot") or "")
        bid = str(rb.id or "")
        if role == "page_number" or mineru_type == "page_number":
            if not page_number[1]:
                page_number = (bid, t)
                if slot in ("aside_left", "aside_right", "main"):
                    page_number_slot = slot
                else:
                    cx = x0
                    if len(bbox) >= 3:
                        cx = (x0 + float(bbox[2])) / 2.0
                    page_number_slot = "aside_left" if cx < 500 else "aside_right"
            continue
        if role == "running_footer" or mineru_type in ("page_footer", "footer"):
            if not footer[1]:
                footer = (bid, t)
            continue
        if role == "running_header" or mineru_type in ("page_header", "header"):
            if len(t) < 40:
                header_items.append((x0, y0, bid, t))
            continue
        # Fallback by vertical position when role missing
        if y0 < 200 and len(t) < 40:
            header_items.append((x0, y0, bid, t))
        elif not page_number[1] and (t.isdigit() or len(t) <= 4):
            page_number = (bid, t)
            cx = (x0 + float(bbox[2])) / 2.0 if len(bbox) >= 3 else x0
            page_number_slot = "aside_left" if cx < 500 else "aside_right"
        elif not footer[1] and len(t) < 40:
            footer = (bid, t)
    header_items.sort(key=lambda it: (round(it[1] / 8.0), it[0]))
    header_parts: list[tuple[str, str]] = []
    for _, _, bid, text in header_items:
        if not header_parts or header_parts[-1][1] != text:
            header_parts.append((bid, text))
    return header_parts, page_number, footer, page_number_slot


def _render_running_header_html(
    header_parts: list[str] | list[tuple[str, str]],
) -> str:
    spans: list[str] = []
    for part in header_parts:
        if isinstance(part, tuple):
            bid, text = part[0], part[1]
        else:
            bid, text = "", part
        t = str(text or "").strip()
        if not t:
            continue
        bid_attr = f' data-block-id="{_escape_html(bid)}"' if bid else ""
        spans.append(
            f'<span class="lb-chrome-hit"{bid_attr}>{_escape_html(t)}</span>'
        )
    if not spans:
        return ""
    return f'<header class="lb-running-hd">{" ".join(spans)}</header>'


def render_page_html(
    blocks: list[DocumentBlock],
    page_no: int,
    *,
    profile_id: str = "hujiao_math_hs_v1",
) -> str:
    page_blocks = [b for b in blocks_for_page(blocks, page_no) if not _should_skip_render(b)]
    running = [
        b for b in blocks_for_page(blocks, page_no) if _layout_zone(b) == "running" and b.text
    ]
    header_parts, page_number, footer, page_number_slot = _pick_running_chrome(running)
    print_to_pdf = _print_page_to_pdf_map(blocks)
    toc_page = _first_toc_page_no(blocks)
    page_is_toc = any(
        (b.meta or {}).get("pedagogy_role") == "toc"
        for b in blocks_for_page(blocks, page_no)
    )

    by_zone: dict[str, list[str]] = {
        "main": [],
        "aside_left": [],
        "aside_right": [],
        "full_bleed": [],
    }

    content_blocks = [b for b in page_blocks if _layout_zone(b) != "running"]
    content_blocks = mark_solution_blocks(content_blocks)
    from app.services.ir.column_detect import aside_gap_em, content_y_band

    band_top, _band_bot = content_y_band(
        [b.bbox for b in content_blocks if b.bbox]
    )
    # Track previous aside bottom (normalized y) per column for margin gaps.
    aside_prev_bottom: dict[str, float] = {
        "aside_left": band_top,
        "aside_right": band_top,
    }
    grouped = _group_figure_blocks(content_blocks)
    for item in grouped:
        if isinstance(item, list):
            html = _render_figure_group_html(item)
            zone = _layout_zone(item[0])
            anchor = item[0]
        else:
            html = render_block_html(item, print_to_pdf=print_to_pdf)
            zone = _layout_zone(item)
            anchor = item
        if not html.strip():
            continue
        bucket = zone if zone in by_zone else "main"
        if bucket in ("aside_left", "aside_right"):
            y0, y1 = _bbox_y0_y1(anchor)
            gap = aside_gap_em(y0, aside_prev_bottom[bucket])
            html = _wrap_aside_item(html, gap)
            aside_prev_bottom[bucket] = max(aside_prev_bottom[bucket], y1)
        by_zone[bucket].append(html)

    return render_zone_grid_html(
        page_no=page_no,
        profile_id=profile_id,
        header_parts=header_parts,
        page_number=page_number[1],
        footer=footer[1],
        by_zone=by_zone,
        toc_page_no=None if page_is_toc else toc_page,
        page_number_slot=page_number_slot,
        page_number_block_id=page_number[0],
        footer_block_id=footer[0],
    )


def polish_page_html(html: str) -> str:
    """Normalize list markers / edge whitespace in already-rendered page HTML.

    Applies to draft chunks served without IR re-render so old imports pick up
    alignment fixes without a full reprocess.
    """
    if not html or "<p" not in html:
        return html
    try:
        from bs4 import BeautifulSoup, NavigableString
    except ImportError:
        return html

    soup = BeautifulSoup(html, "html.parser")
    for p in soup.find_all("p"):
        plain = p.get_text()
        if not is_numbered_list_item(plain) and not plain.strip(_EDGE_WS):
            # Still strip empty edge text nodes on ordinary paragraphs
            pass
        # Strip leading/trailing whitespace-only text nodes
        if p.contents:
            first = p.contents[0]
            if isinstance(first, NavigableString):
                stripped = str(first).lstrip(_EDGE_WS)
                if stripped != str(first):
                    first.replace_with(stripped)
            last = p.contents[-1]
            if isinstance(last, NavigableString):
                stripped = str(last).rstrip(_EDGE_WS)
                if stripped != str(last):
                    last.replace_with(stripped)
        plain2 = p.get_text()
        if is_numbered_list_item(plain2):
            classes = p.get("class") or []
            if isinstance(classes, str):
                classes = classes.split()
            # Don't override solution answer styling with question list indent
            if (
                "lb-solution-item" not in classes
                and "lb-solution-body-p" not in classes
                and "lb-solution-lead" not in classes
                and "lb-list-item" not in classes
            ):
                p["class"] = list(classes) + ["lb-list-item"]
            # Fix marker in the first text node
            for node in p.contents:
                if isinstance(node, NavigableString) and str(node).strip(_EDGE_WS):
                    raw = str(node)
                    lead_ws_len = len(raw) - len(raw.lstrip(_EDGE_WS))
                    body = raw[lead_ws_len:]
                    if _NUMBERED_LIST_MARKER_RE.match(body):
                        fixed = normalize_numbered_list_marker(body)
                        node.replace_with(raw[:lead_ws_len] + fixed)
                    break
    return str(soup)


def sanitize_page_html(html: str) -> str:
    html = polish_page_html(html)
    try:
        import bleach
    except ImportError:
        return html
    # Bleach strips style and can drop the closing </div> if we only protect the
    # opening tag — protect each full ``lb-aside-item`` element (balanced divs).
    protected: list[str] = []
    out: list[str] = []
    i = 0
    marker = '<div class="lb-aside-item"'
    while i < len(html):
        j = html.find(marker, i)
        if j < 0:
            out.append(html[i:])
            break
        out.append(html[i:j])
        depth = 0
        k = j
        end = -1
        while k < len(html):
            open_at = html.find("<div", k)
            close_at = html.find("</div>", k)
            if close_at < 0:
                break
            if open_at >= 0 and open_at < close_at:
                depth += 1
                gt = html.find(">", open_at)
                k = len(html) if gt < 0 else gt + 1
                continue
            depth -= 1
            k = close_at + len("</div>")
            if depth == 0:
                end = k
                break
        if end < 0:
            out.append(html[j:])
            break
        protected.append(html[j:end])
        out.append(f"__LB_ASIDE_ITEM_{len(protected) - 1}__")
        i = end
    html_protected = "".join(out)

    allowed_tags = bleach.sanitizer.ALLOWED_TAGS.union(
        {
            "article",
            "header",
            "footer",
            "section",
            "aside",
            "nav",
            "figure",
            "figcaption",
            "img",
            "table",
            "caption",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "h1",
            "h2",
            "h3",
            "h4",
            "span",
            "div",
            "p",
            "br",
            "ol",
            "ul",
            "li",
            "a",
        }
    )
    allowed_attrs = {
        **bleach.sanitizer.ALLOWED_ATTRIBUTES,
        "*": [
            "class",
            "data-page",
            "data-print-page",
            "data-profile",
            "data-zone",
            "data-block-id",
            "data-anchor",
            "data-figure-group",
            "data-latex",
            "data-goto-page",
            "data-goto-print-page",
            "data-lb-gap",
            "role",
            "tabindex",
            "title",
        ],
        "img": ["src", "alt", "class"],
        "a": ["href", "class", "data-goto-page", "data-goto-print-page", "title"],
        "li": ["class", "data-goto-page", "data-goto-print-page", "role", "tabindex", "title"],
        "span": [
            "class",
            "data-goto-page",
            "data-goto-print-page",
            "role",
            "tabindex",
            "title",
        ],
        "div": ["class", "data-lb-gap", "data-zone", "aria-hidden"],
        # Textbook tables (e.g. 表6-3) need merged header cells.
        "td": ["class", "rowspan", "colspan", "align"],
        "th": ["class", "rowspan", "colspan", "align", "scope"],
    }
    cleaned = bleach.clean(
        html_protected, tags=list(allowed_tags), attributes=allowed_attrs, strip=False
    )
    for idx, tag in enumerate(protected):
        cleaned = cleaned.replace(f"__LB_ASIDE_ITEM_{idx}__", tag)
    return cleaned


def html_to_plain_text(html: str) -> str:
    try:
        from html.parser import HTMLParser
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html)

    class _Strip(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data: str) -> None:
            if data.strip():
                self.parts.append(data.strip())

    parser = _Strip()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    return "\n".join(parser.parts)


def render_document_html(blocks: list[DocumentBlock], *, profile_id: str = "hujiao_math_hs_v1") -> str:
    pages = sorted({b.page_no for b in blocks})
    return "\n".join(render_page_html(blocks, p, profile_id=profile_id) for p in pages)


def layout_zone(block: DocumentBlock) -> str:
    return _layout_zone(block)


def is_layout_atomic(block: DocumentBlock) -> bool:
    zone = _layout_zone(block)
    role = _pedagogy_role(block)
    if zone in ("aside_right", "aside_left") and role != "body":
        return True
    if (block.meta or {}).get("figure_group_id") and block.type == "figure":
        return True
    return False


def document_block_to_dict(block: DocumentBlock) -> dict[str, Any]:
    return block.model_dump()


def document_blocks_from_dicts(raw: list[dict[str, Any]]) -> list[DocumentBlock]:
    return [DocumentBlock.model_validate(item) for item in raw]
