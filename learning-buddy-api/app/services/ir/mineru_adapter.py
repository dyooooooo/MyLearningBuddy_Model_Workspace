"""MinerU content_list / content_list_v2 → unified DocumentBlock IR.

Official outputs (MinerU ≥3.0 / Cloud zip):
  - ``*_content_list.json`` — flat reading-order list (v1)
  - ``*_content_list_v2.json`` — page-grouped ``type + content`` (v2)
  - ``layout.json`` ≈ middle.json (debug / layout; not primary IR source)

Docs: https://opendatalab.github.io/MinerU/reference/output_files/
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse, parse_qs

from app.services.document_ir import (
    DocumentBlock,
    bbox_y0,
    heading_level_from_text,
    is_chapter_text,
    is_lesson_text,
    is_numbered_list_item,
    normalize_block_text,
    normalize_chapter_title,
    normalize_figure_caption,
    normalize_numbered_list_marker,
    stable_block_id,
)

_CAPTION_KEYS = (
    "image_caption",
    "table_caption",
    "chart_caption",
    "caption",
    "code_caption",
    "algorithm_caption",
)

# MinerU often puts the visible note in footnote when caption is empty
# (e.g. portrait bio: image_caption=[] + image_footnote=[...]).
_FOOTNOTE_KEYS = (
    "image_footnote",
    "table_footnote",
    "chart_footnote",
    "footnote",
)

_SKIP_TYPES = frozenset(
    {
        "page_footnote",
        "discarded",
    }
)

# Running chrome: keep in IR, render as page header / page number (not body).
_RUNNING_TYPES = frozenset(
    {
        "page_header",
        "page_number",
        "page_footer",
        "footer",
        "header",  # short running headers; long ones may still become titles below
    }
)


def _bbox_id_key(bbox: object) -> str:
    """Stable geometry salt for block ids (ignore OCR text churn)."""
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return ""
    try:
        return ",".join(str(int(round(float(x)))) for x in bbox[:4])
    except (TypeError, ValueError):
        return ""


def _caption_lines(raw: object) -> str:
    if isinstance(raw, list):
        parts: list[str] = []
        for x in raw:
            if isinstance(x, dict):
                t = _spans_to_text(x) or str(x.get("content") or "").strip()
                if t:
                    parts.append(t)
            else:
                s = str(x).strip()
                if s:
                    parts.append(s)
        return "\n".join(parts)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""


def _spans_to_text(node: object) -> str:
    """Flatten MinerU v2 span lists / nested content dicts to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (int, float)):
        return str(node)
    if isinstance(node, list):
        return "".join(_spans_to_text(x) for x in node).strip()
    if not isinstance(node, dict):
        return str(node).strip()

    # Prefer explicit content / children over dumping the whole dict.
    if "children" in node and isinstance(node["children"], list):
        child = _spans_to_text(node["children"])
        if child:
            return child
    for key in (
        "content",
        "text",
        "item_content",
        "title_content",
        "paragraph_content",
        "math_content",
        "code_content",
        "algorithm_content",
        "page_aside_text_content",
        "page_footnote_content",
        "page_header_content",
        "page_footer_content",
        "page_number_content",
    ):
        if key in node:
            t = _spans_to_text(node[key])
            if t:
                return t
    return ""


def _content_dict(raw: dict[str, Any]) -> dict[str, Any]:
    c = raw.get("content")
    return c if isinstance(c, dict) else {}


_TOC_TRAILING_PAGE_RE = re.compile(
    r"^(?P<label>.+?)(?:\s*[\.．…·•]+)*\s+(?P<page>\d{1,4})\s*$"
)
# Defaults from Edition seed (hujiao); call sites may reload via patterns_for(edition_id).
from app.services.editions.bundle import SEED_HUJIAO
from app.services.editions.patterns import patterns_for as _edition_patterns_for

_p0 = _edition_patterns_for(SEED_HUJIAO)
_CHAPTER_TOC_RE = _p0.chapter_toc
_TOC_SECTION_RE = _p0.toc_section
_TOC_SUMMARY_RE = _p0.toc_summary
_TOC_TITLES = set(_p0.toc_titles)
_FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
# MinerU types that may carry a VLM-flattened TOC as plain text.
_TOC_TEXT_TYPES = frozenset({"text", "paragraph", "list", "title", ""})


def _normalize_toc_digits(text: str) -> str:
    return (text or "").translate(_FULLWIDTH_DIGIT_TRANS)


def _list_item_text(item: object) -> str:
    """Extract one list/index item label from v1 string or v2 item_content dict."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, (int, float)):
        return str(item)
    if isinstance(item, list):
        return _spans_to_text(item)
    if not isinstance(item, dict):
        return str(item).strip()
    for key in ("item_content", "content", "text", "title"):
        if key in item:
            t = _spans_to_text(item[key])
            if t:
                return t
    return _spans_to_text(item)


def _parse_toc_entry(raw_text: str) -> dict[str, Any]:
    """Split TOC line: last number is always the printed page (书页码).

    Examples:
      ``1.1 集合初步 2`` → label=1.1 集合初步, print_page=2
      ``第1章 集合与逻辑`` → label only (no page)
    """
    text = _normalize_toc_digits((raw_text or "").strip())
    # VLM sometimes keeps LaTeX-escaped stars: ``\*5.4 反函数 136``
    if text.startswith("\\*"):
        text = "*" + text[2:]
    m = _TOC_TRAILING_PAGE_RE.match(text)
    if m:
        label = m.group("label").strip(" .\u3000．…·•")
        page = int(m.group("page"))
        # Guard: don't treat a bare number line as title+page
        if label and not label.isdigit():
            is_chapter = bool(_CHAPTER_TOC_RE.match(label))
            if is_chapter:
                level = 0
            elif re.match(r"^\*?\d+\.\d+\.\d+\b", label):
                level = 2
            else:
                level = 1
            return {
                "label": label,
                "print_page": page,
                "is_chapter": is_chapter,
                "level": level,
            }
    is_chapter = bool(_CHAPTER_TOC_RE.match(text))
    level = 0 if is_chapter else 1
    if not is_chapter and re.match(r"^\*?\d+\.\d+\.\d+\b", text):
        level = 2
    return {
        "label": text,
        "print_page": None,
        "is_chapter": is_chapter,
        "level": level,
    }


def _iter_toc_raw_lines(text: str) -> list[str]:
    """Split VLM/pipeline TOC blobs into candidate entry lines."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # Some VLM dumps use `` | `` between entries on one visual line.
    raw = re.sub(r"\s*\|\s*", "\n", raw)
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-•· ").strip()
        if not line:
            continue
        out.append(_normalize_toc_digits(line))
    return out


def _is_plausible_toc_line(line: str) -> bool:
    """True if a single line looks like a textbook TOC entry."""
    s = (line or "").strip()
    if not s or s in _TOC_TITLES:
        return False
    if s.startswith("\\*"):
        s = "*" + s[2:]
    if _CHAPTER_TOC_RE.match(s):
        return True
    if _TOC_SUMMARY_RE.match(s):
        return True
    if _TOC_SECTION_RE.match(s):
        return True
    ent = _parse_toc_entry(s)
    if ent.get("print_page") is not None and ent.get("label"):
        label = str(ent["label"])
        if _CHAPTER_TOC_RE.match(label) or _TOC_SECTION_RE.match(label) or _TOC_SUMMARY_RE.match(label):
            return True
    return False


def _looks_like_toc_text(text: str) -> bool:
    """Detect VLM-flattened TOC (multi-line text) vs ordinary body/chapter.

    Pipeline emits ``type=index``; VLM often emits one ``type=text`` blob that
    starts with ``第N章`` — without this check, ``normalize_chapter_title``
    truncates the whole outline to a single chapter line.
    """
    lines = _iter_toc_raw_lines(text)
    if not lines:
        return False
    tocish = [ln for ln in lines if _is_plausible_toc_line(ln)]
    if len(lines) == 1:
        ent = _parse_toc_entry(lines[0])
        return bool(
            ent.get("print_page") is not None
            and ent.get("label")
            and (
                _TOC_SECTION_RE.match(str(ent["label"]))
                or _TOC_SUMMARY_RE.match(str(ent["label"]))
                or ent.get("is_chapter")
            )
        )
    if len(tocish) < 2:
        return False
    return (len(tocish) / len(lines)) >= 0.55


def _toc_entries_from_text(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for ln in _iter_toc_raw_lines(text):
        if ln in _TOC_TITLES:
            continue
        if not _is_plausible_toc_line(ln) and not ln.strip():
            continue
        # Keep chapter-only / section lines even if heuristic is soft once
        # the parent block already looked like TOC.
        if _is_plausible_toc_line(ln) or _CHAPTER_TOC_RE.match(ln) or _TOC_SECTION_RE.match(ln):
            entries.append(_parse_toc_entry(ln))
    return entries


def _apply_toc_meta(
    meta: dict[str, Any],
    *,
    entries: list[dict[str, Any]],
    source: str,
) -> None:
    meta["pedagogy_role"] = "toc"
    meta["toc_entries"] = entries
    meta["toc_source"] = source


def _promote_vlm_toc_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Page-level: promote leftover text/heading chunks on TOC pages.

    VLM often splits one TOC page into several ``text`` blocks (continuation
    lines + bare chapter titles). Once the page is known to be TOC, promote
    those siblings so they also render via ``.lb-toc``.

    Do **not** treat a normal chapter page (章标题 + 1.1 节标题、无行末页码)
    as TOC — that was a false-positive path.
    """
    by_page: dict[int, list[DocumentBlock]] = {}
    for b in blocks:
        by_page.setdefault(int(b.page_no), []).append(b)

    for page_blocks in by_page.values():
        has_toc = False
        numbered_singles = 0
        for b in page_blocks:
            meta = b.meta or {}
            text = (b.text or "").strip()
            if meta.get("pedagogy_role") == "toc":
                has_toc = True
            if text in _TOC_TITLES:
                has_toc = True
            if meta.get("pedagogy_role") == "running_header" and text in _TOC_TITLES:
                has_toc = True
            if meta.get("skip_render"):
                continue
            if _looks_like_toc_text(text):
                has_toc = True
                continue
            # Only page-numbered singles count toward "this page is a TOC"
            if "\n" not in text and _is_plausible_toc_line(text):
                ent = _parse_toc_entry(text)
                if ent.get("print_page") is not None:
                    numbered_singles += 1
        if not has_toc and numbered_singles >= 2:
            has_toc = True
        if not has_toc:
            continue

        for b in page_blocks:
            meta = b.meta or {}
            if meta.get("pedagogy_role") == "toc":
                continue
            if meta.get("skip_render") or meta.get("layout_zone") == "running":
                continue
            text = (b.text or "").strip()
            if not text or text in _TOC_TITLES:
                continue
            if not (
                _looks_like_toc_text(text)
                or _is_plausible_toc_line(text)
            ):
                continue
            entries = _toc_entries_from_text(text)
            if not entries:
                entries = [_parse_toc_entry(text)]
            if not entries:
                continue
            b.type = "list"  # type: ignore[assignment]
            if b.meta is None:
                b.meta = {}
            _apply_toc_meta(b.meta, entries=entries, source="vlm_text_page")
            b.text = "\n".join(
                str(e.get("label") or "")
                + (f" {e['print_page']}" if e.get("print_page") is not None else "")
                for e in entries
            ).strip() or text

    return blocks


def _extract_list_items(raw: dict[str, Any]) -> list[str]:
    cd = _content_dict(raw)
    items = cd.get("list_items") or raw.get("list_items")
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        t = _list_item_text(item)
        if t:
            out.append(t)
    return out


def _extract_text(raw: dict[str, Any]) -> str:
    """Extract display text from v1 flat fields or v2 nested content."""
    # v1 flat
    for key in ("text",):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    content = raw.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    cd = _content_dict(raw)
    btype = str(raw.get("type") or "")

    # v2 typed payloads
    type_keys = {
        "title": ("title_content",),
        "paragraph": ("paragraph_content",),
        "equation_interline": ("math_content",),
        "equation": ("math_content", "text"),
        "code": ("code_content",),
        "algorithm": ("algorithm_content",),
        "page_aside_text": ("page_aside_text_content",),
        "aside_text": ("page_aside_text_content", "content"),
        "list": ("list_items",),
        "index": ("list_items",),
        "page_header": ("page_header_content",),
        "header": ("page_header_content", "text"),
        "page_number": ("page_number_content",),
        "page_footer": ("page_footer_content",),
        "footer": ("page_footer_content", "text"),
    }
    for key in type_keys.get(btype, ()):
        if key == "list_items":
            lines = _extract_list_items(raw)
            if lines:
                return "\n".join(f"- {ln}" for ln in lines)
            continue
        t = _spans_to_text(cd.get(key))
        if t:
            return t

    # Generic fallback over content dict then top-level
    t = _spans_to_text(cd) if cd else ""
    if t:
        return t
    if isinstance(content, dict):
        return _spans_to_text(content)
    return ""


def _extract_table_html(raw: dict[str, Any]) -> str:
    for key in ("table_body", "html"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    cd = _content_dict(raw)
    for key in ("table_body", "html", "table_html"):
        val = cd.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _extract_caption_or_footnote(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        cap = _caption_lines(raw.get(key))
        if cap:
            return cap
    cd = _content_dict(raw)
    for key in keys:
        cap = _caption_lines(cd.get(key))
        if cap:
            return cap
    for key in keys:
        t = _spans_to_text(cd.get(key))
        if t:
            return t
    return ""


def _extract_caption(raw: dict[str, Any]) -> str:
    """Prefer image/table caption; fall back to footnote when caption is empty."""
    caption = _extract_caption_or_footnote(raw, _CAPTION_KEYS)
    footnote = _extract_caption_or_footnote(raw, _FOOTNOTE_KEYS)
    if caption and footnote and footnote not in caption:
        return f"{caption}\n{footnote}"
    return caption or footnote


def _extract_img_path(raw: dict[str, Any]) -> str:
    """Resolve image path from v1 flat fields or v2 ``content.image_source.path``."""
    for key in ("img_path", "image_path", "src"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    cd = _content_dict(raw)
    for key in ("img_path", "image_path", "src", "path"):
        val = cd.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # MinerU Cloud content_list_v2: content.image_source.path = "images/….jpg"
    src = cd.get("image_source")
    if isinstance(src, dict):
        for key in ("path", "img_path", "image_path", "src"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(src, str) and src.strip():
        return src.strip()
    return ""


def _resolve_relative_image_path(img_path: str, asset_prefix: str) -> str:
    """Map Cloud zip-relative ``images/foo.jpg`` to MinIO key under job assets."""
    if not img_path or not asset_prefix:
        return img_path
    normalized = img_path.replace("\\", "/").lstrip("./")
    if normalized.startswith("images/"):
        name = normalized.split("/", 1)[1]
        if name:
            return f"{asset_prefix.rstrip('/')}/mineru/{name}"
    return img_path


def _extract_asset_key(
    img_path: str, *, asset_prefix: str = ""
) -> tuple[str | None, str | None]:
    if not img_path or img_path.startswith("http://minio"):
        return None, img_path or None
    img_path = _resolve_relative_image_path(img_path, asset_prefix)
    if img_path.startswith("/api/v1/studio/proxy/import/asset"):
        parsed = urlparse(img_path)
        qs = parse_qs(parsed.query)
        keys = qs.get("key") or []
        if keys:
            return unquote(keys[0]), img_path
    if img_path.startswith("corpus/"):
        return img_path, None
    return None, img_path


def _normalize_bbox(raw: object) -> list[float] | None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        try:
            return [float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])]
        except (TypeError, ValueError):
            return None
    return None


def _map_mineru_type(raw: dict[str, Any], text: str) -> str:
    btype = str(raw.get("type") or "text")
    # Running chrome → paragraph + layout_zone=running (set in converter).
    if btype in ("page_header", "page_number", "page_footer", "footer"):
        return "paragraph"
    # Bare "header": short → running chrome; long chapter-like → title/heading.
    if btype == "header":
        if len(text.strip()) < 40 and not is_chapter_text(text) and not is_lesson_text(text):
            return "paragraph"
        return "title" if is_chapter_text(text) else "heading"
    if btype in ("aside_text", "page_aside_text"):
        return "paragraph"
    if btype in ("image", "chart"):
        return "figure"
    if btype in ("equation", "equation_interline"):
        return "equation"
    if btype == "table":
        return "table"
    if btype in ("code", "algorithm"):
        return "paragraph"
    if btype in ("list", "index"):
        return "list"
    if btype == "title":
        return "title" if is_chapter_text(text) else "heading"
    # v1 text with text_level
    text_level = raw.get("text_level")
    if btype == "text" and isinstance(text_level, int) and text_level >= 1:
        return "title" if text_level == 1 and is_chapter_text(text) else "heading"
    if text.strip():
        if is_chapter_text(text):
            return "title"
        if is_lesson_text(text):
            return "heading"
        if text.strip().startswith(("- ", "* ", "1.")):
            return "list"
        if is_numbered_list_item(text):
            return "list"
    return "paragraph"


def _running_meta(mineru_type: str) -> dict[str, Any] | None:
    """Return layout meta for page chrome, or None if not a running element."""
    if mineru_type in ("page_header",):
        return {
            "layout_zone": "running",
            "pedagogy_role": "running_header",
            "skip_render": True,
        }
    if mineru_type == "header":
        return {
            "layout_zone": "running",
            "pedagogy_role": "running_header",
            "skip_render": True,
        }
    if mineru_type == "page_number":
        return {
            "layout_zone": "running",
            "pedagogy_role": "page_number",
            "skip_render": True,
        }
    if mineru_type in ("page_footer", "footer"):
        return {
            "layout_zone": "running",
            "pedagogy_role": "running_footer",
            "skip_render": True,
        }
    return None


def _should_skip_mineru_block(raw: dict[str, Any], text: str) -> bool:
    mineru_type = str(raw.get("type") or "")
    if mineru_type in _SKIP_TYPES:
        return True
    # Empty running chrome is useless
    if mineru_type in _RUNNING_TYPES and not text.strip():
        return True
    if mineru_type == "text" and text.strip() in ("#",):
        return True
    if mineru_type == "text" and len(text.strip()) <= 2 and text.strip().isdigit():
        return True
    return False


def _normalize_block_text(text: str, btype: str) -> str:
    t = normalize_block_text(text)
    if btype == "title":
        return normalize_chapter_title(t)
    if is_numbered_list_item(t):
        return normalize_numbered_list_marker(t)
    return t


def _heading_level(raw: dict[str, Any], text: str, btype: str) -> int | None:
    if btype not in ("title", "heading"):
        return None
    cd = _content_dict(raw)
    for src in (cd, raw):
        lvl = src.get("level")
        if isinstance(lvl, int) and lvl >= 1:
            return lvl
        tl = src.get("text_level")
        if isinstance(tl, int) and tl >= 1:
            return tl
    return heading_level_from_text(text) or (1 if btype == "title" else 2)


def _sort_mineru_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = list(enumerate(blocks))

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        idx, block = item
        page_idx = block.get("page_idx")
        page_no = int(page_idx) + 1 if isinstance(page_idx, int) and page_idx >= 0 else 1
        return (page_no, bbox_y0(block), idx)

    return [b for _, b in sorted(indexed, key=sort_key)]


def mineru_content_list_to_ir(
    content_list: list[dict[str, Any]],
    *,
    engine: str = "mineru_cloud",
    asset_prefix: str = "",
) -> list[DocumentBlock]:
    """Convert MinerU content_list (v1 flat or flattened v2) to DocumentBlock list.

    ``asset_prefix`` maps leftover relative ``images/…`` paths to MinIO keys
    under ``{prefix}/mineru/`` (Cloud zip images uploaded by mineru_cloud).
    """
    out: list[DocumentBlock] = []
    order = 0
    for raw in _sort_mineru_blocks(content_list):
        if not isinstance(raw, dict):
            continue
        page_idx = raw.get("page_idx")
        page_no = int(page_idx) + 1 if isinstance(page_idx, int) and page_idx >= 0 else 1
        text = _extract_text(raw)
        if _should_skip_mineru_block(raw, text):
            continue
        table_html = _extract_table_html(raw)
        caption = _extract_caption(raw)
        mineru_type = str(raw.get("type") or "")

        # Detect TOC *before* type mapping: VLM text blobs often start with
        # ``第N章`` and would be truncated by ``normalize_chapter_title``.
        structured_toc = False
        vlm_toc = False
        toc_entries: list[dict[str, Any]] = []
        toc_source = ""
        if mineru_type == "index" or (
            mineru_type == "list"
            and str(_content_dict(raw).get("list_type") or "") == "text_list"
        ):
            lines = _extract_list_items(raw)
            toc_entries = [_parse_toc_entry(ln) for ln in lines if ln]
            if toc_entries:
                structured_toc = True
                toc_source = "index" if mineru_type == "index" else "list"
                text = "\n".join(ln for ln in lines if ln)
        elif mineru_type in _TOC_TEXT_TYPES and _looks_like_toc_text(text):
            toc_entries = _toc_entries_from_text(text)
            if toc_entries:
                vlm_toc = True
                toc_source = "vlm_text"
                text = "\n".join(
                    str(e.get("label") or "")
                    + (
                        f" {e['print_page']}"
                        if e.get("print_page") is not None
                        else ""
                    )
                    for e in toc_entries
                )

        if structured_toc or vlm_toc:
            btype = "list"
        else:
            btype = _map_mineru_type(raw, text or caption)
            text = _normalize_block_text(text, btype)

        if btype == "figure":
            caption = normalize_figure_caption(caption) or None
        img_path = _extract_img_path(raw)
        asset_key, asset_url = _extract_asset_key(img_path, asset_prefix=asset_prefix)

        level = None if (structured_toc or vlm_toc) else _heading_level(
            raw, text or caption or "", btype
        )

        block_id = stable_block_id(
            engine,
            str(page_no),
            str(order),
            str(raw.get("type") or ""),
            # Prefer geometry over text so OCR corrections / re-adapt don't rename ids.
            _bbox_id_key(raw.get("bbox"))
            or (text or table_html or img_path or "")[:80],
        )
        order += 1
        meta: dict[str, Any] = {
            "engine": engine,
            "mineru_type": raw.get("type"),
        }
        if raw.get("sub_type") is not None:
            meta["mineru_sub_type"] = raw.get("sub_type")
        # Page chrome: keep in IR but render via header/footer slots.
        if btype == "paragraph":
            run = _running_meta(mineru_type)
            if run:
                meta.update(run)
        if toc_entries:
            _apply_toc_meta(meta, entries=toc_entries, source=toc_source)

        # Drop empty list/index shells (no usable text)
        if btype == "list" and not (text or "").strip():
            continue

        out.append(
            DocumentBlock(
                id=block_id,
                type=btype,  # type: ignore[arg-type]
                page_no=page_no,
                order=order,
                bbox=_normalize_bbox(raw.get("bbox")),
                text=text or None,
                table_html=table_html or None,
                asset_key=asset_key,
                asset_url=asset_url if asset_url and asset_url.startswith("/") else None,
                caption=caption or None,
                level=level,
                meta=meta,
            )
        )
        if asset_url and not asset_url.startswith("/") and not out[-1].asset_url:
            out[-1].asset_url = asset_url
    return _promote_vlm_toc_blocks(out)
