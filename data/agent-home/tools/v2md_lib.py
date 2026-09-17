"""Shared helpers for the isolated V2 Markdown line.

Library only — not in manifest.yaml. No ``app.*`` imports.
Reads MinerU Cloud ``*_content_list_v2.json`` and writes only under ``v2md/``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_BODY_TYPES = frozenset(
    {
        "title",
        "heading",
        "page_title",
        "paragraph",
        "list",
        "table",
        "image",
        "chart",
        "equation",
        "equation_interline",
    }
)
_HEADER_TYPES = frozenset({"page_header", "header"})
_FOOTER_TYPES = frozenset({"page_footer", "footer"})
_PAGE_NO_TYPES = frozenset({"page_number", "page-number"})
_FOOTNOTE_TYPES = frozenset({"page_footnote"})
_INLINE_EQ = frozenset({"equation_inline", "inline_equation"})
_BLOCK_EQ = frozenset({"equation_interline", "interline_equation", "equation"})
_CONTENT_KEYS = (
    "paragraph_content",
    "title_content",
    "page_header_content",
    "page_footer_content",
    "page_number_content",
    "page_footnote_content",
    "math_content",
    "item_content",
)
def safe_book_id(book_id: str) -> str:
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    return sid


def book_dir(book_id: str, root: str = "") -> Path:
    sid = safe_book_id(book_id)
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir() and not (base / sid).is_dir():
        return base / "textbooks" / sid
    return base / sid


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def asset_url(book_id: str, path: str) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if not raw:
        return ""
    if raw.startswith("/api/") or raw.startswith("http://") or raw.startswith("https://"):
        return raw
    name = raw.rsplit("/", 1)[-1]
    key = f"kb/pdfs/{safe_book_id(book_id)}/assets/mineru/{name}"
    return f"/api/v1/kb/proxy/asset?key={quote(key, safe='')}"


def rewrite_image_urls(markdown: str, book_id: str) -> str:
    if not markdown:
        return markdown

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group(1)}{asset_url(book_id, match.group(2))}{match.group(3)}"

    return re.sub(r"(!\[[^\]]*\]\()(images/[^)]+)(\))", _repl, markdown)


def find_v2_path(book: Path) -> Path | None:
    folder = book / "mineru"
    if not folder.is_dir():
        return None
    hits = sorted(folder.glob("*_content_list_v2.json"))
    return hits[0] if hits else None


def load_v2_pages(book: Path) -> dict[int, list[dict[str, Any]]]:
    path = find_v2_path(book)
    if path is None:
        raise FileNotFoundError(f"missing content_list_v2 under {book / 'mineru'}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("content_list_v2 is empty")
    if isinstance(data[0], list):
        out: dict[int, list[dict[str, Any]]] = {}
        for idx, page in enumerate(data):
            if isinstance(page, list):
                out[idx + 1] = [b for b in page if isinstance(b, dict)]
        return out
    grouped: dict[int, list[dict[str, Any]]] = {}
    for block in data:
        if not isinstance(block, dict):
            continue
        raw = block.get("page_idx", block.get("page_index", 0))
        try:
            page_no = int(raw) + 1
        except (TypeError, ValueError):
            page_no = 1
        grouped.setdefault(page_no, []).append(block)
    return dict(sorted(grouped.items()))


def read_meta(book: Path) -> dict[str, Any]:
    path = book / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def print_page_of(book: Path, page_no: int) -> int | None:
    toc_path = book / "toc.json"
    if not toc_path.is_file():
        return None
    try:
        toc = json.loads(toc_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(toc, dict):
        return None
    entries = toc.get("entries")
    if isinstance(entries, list):
        for row in entries:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("pdf_page") or 0) == page_no:
                    raw = row.get("print_page")
                    if raw is None or raw == "":
                        continue
                    return int(raw)
            except (TypeError, ValueError):
                continue
    mapping = toc.get("print_to_pdf")
    if isinstance(mapping, dict):
        for print_no, pdf_no in mapping.items():
            try:
                if int(pdf_no) == page_no:
                    return int(print_no)
            except (TypeError, ValueError):
                continue
    return None


def _as_spans(value: object) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict) and ("type" in value or "content" in value):
        return [value]
    if isinstance(value, str) and value.strip():
        return [{"type": "text", "content": value}]
    return []


def _block_spans(block: dict[str, Any]) -> list[dict[str, Any]]:
    content = block.get("content")
    if isinstance(content, dict):
        for key in _CONTENT_KEYS:
            if key in content:
                return _as_spans(content.get(key))
        if isinstance(content.get("content"), str):
            return _as_spans(content.get("content"))
    if isinstance(block.get("text"), str):
        return _as_spans(block.get("text"))
    return _as_spans(content)


def merge_spans(spans: list[dict[str, Any]]) -> str:
    bits: list[str] = []
    for span in spans:
        kind = str(span.get("type") or "text").strip().lower()
        text = span.get("content")
        if not isinstance(text, str):
            text = ""
        if kind in _INLINE_EQ:
            inner = text.strip()
            if not inner:
                continue
            if inner.startswith("$") and inner.endswith("$"):
                bits.append(inner)
            else:
                bits.append(f"${inner}$")
            continue
        if kind in _BLOCK_EQ:
            inner = text.strip().strip("$").strip()
            if inner:
                bits.append(f"$$\n{inner}\n$$")
            continue
        piece = text.strip()
        if piece:
            bits.append(piece)
    return " ".join(bits)


def block_plain(block: dict[str, Any]) -> str:
    return merge_spans(_block_spans(block))


def _bbox(block: dict[str, Any]) -> tuple[float, float, float, float]:
    raw = block.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return (0.0, 0.0, 0.0, 0.0)
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)


def _sort_chrome(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(blocks, key=lambda b: (_bbox(b)[1], _bbox(b)[0]))


def _image_path(block: dict[str, Any]) -> str:
    for key in ("img_path", "image_path", "src"):
        val = block.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    content = block.get("content")
    if not isinstance(content, dict):
        return ""
    src = content.get("image_source")
    if isinstance(src, dict):
        for key in ("path", "img_path", "image_path", "src"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(src, str) and src.strip():
        return src.strip()
    for key in ("img_path", "image_path", "src", "path"):
        val = content.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _caption_text(raw: object) -> str:
    if isinstance(raw, list):
        return merge_spans(_as_spans(raw))
    if isinstance(raw, dict):
        return merge_spans(_as_spans(raw))
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _image_markdown(block: dict[str, Any], book_id: str, *, alt_default: str) -> str:
    path = _image_path(block)
    if not path:
        return ""
    content = block.get("content") if isinstance(block.get("content"), dict) else {}
    caps = []
    for key in ("image_caption", "chart_caption", "table_caption", "caption", "image_footnote", "chart_footnote"):
        cap = _caption_text(block.get(key) or content.get(key))
        if cap:
            caps.append(cap)
    alt = " · ".join(caps) or alt_default
    url = asset_url(book_id, path)
    md = f"![{alt}]({url})"
    if caps:
        return f"{md}\n\n*{caps[0]}*"
    return md


def _equation_markdown(block: dict[str, Any]) -> str:
    content = block.get("content") if isinstance(block.get("content"), dict) else {}
    latex = content.get("math_content") if isinstance(content, dict) else ""
    if not isinstance(latex, str) or not latex.strip():
        latex = block_plain(block)
    inner = (latex or "").strip()
    if inner.startswith("$$") and inner.endswith("$$"):
        inner = inner[2:-2].strip()
    elif inner.startswith("$") and inner.endswith("$") and "\n" not in inner:
        inner = inner[1:-1].strip()
    if not inner:
        return ""
    return f"$$\n{inner}\n$$"


def _list_markdown(block: dict[str, Any]) -> str:
    content = block.get("content") if isinstance(block.get("content"), dict) else {}
    items = content.get("list_items") if isinstance(content, dict) else None
    if not isinstance(items, list):
        items = block.get("list_items")
    lines: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                text = merge_spans(_as_spans(item.get("item_content")))
            else:
                text = str(item).strip()
            if text:
                lines.append(f"- {text}")
    if not lines:
        text = block_plain(block)
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def _table_markdown(block: dict[str, Any], book_id: str) -> str:
    content = block.get("content") if isinstance(block.get("content"), dict) else {}
    cap = _caption_text(block.get("table_caption") or content.get("table_caption") or block.get("caption"))
    foot = _caption_text(block.get("table_footnote") or content.get("table_footnote"))
    html = (
        block.get("table_body")
        or block.get("html")
        or block.get("table_html")
        or content.get("table_body")
        or content.get("html")
        or content.get("table_html")
        or ""
    )
    parts: list[str] = []
    if cap:
        parts.append(f"**{cap}**")
    if isinstance(html, str) and html.strip().startswith("<"):
        parts.append(html.strip())
    elif _image_path(block):
        parts.append(_image_markdown(block, book_id, alt_default=cap or "表格"))
    elif cap:
        parts.append(cap)
    if foot:
        parts.append(f"*{foot}*")
    return "\n\n".join(p for p in parts if p)


def render_body_block(block: dict[str, Any], book_id: str) -> str:
    btype = str(block.get("type") or "").strip().lower()
    if btype in _BLOCK_EQ:
        return _equation_markdown(block)
    if btype in {"image", "chart"}:
        return _image_markdown(block, book_id, alt_default="插图")
    if btype == "table":
        return _table_markdown(block, book_id)
    if btype == "list":
        return _list_markdown(block)
    text = block_plain(block)
    if btype in {"title", "heading", "page_title"} and text:
        content = block.get("content") if isinstance(block.get("content"), dict) else {}
        try:
            level = int(content.get("level") or block.get("level") or 2)
        except (TypeError, ValueError):
            level = 2
        level = min(6, max(1, level))
        return f"{'#' * level} {text}"
    return text


def _callout(kind: str, lines: list[str]) -> str:
    body = [ln for ln in (x.strip() for x in lines) if ln]
    if not body:
        return ""
    out = [f"> [!{kind}]"]
    out.extend(f"> {ln}" for ln in body)
    return "\n".join(out)


def _yaml_escape(value: str) -> str:
    text = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def page_frontmatter(
    *,
    book_id: str,
    page_no: int | None,
    print_page: int | None,
    tags: list[str],
) -> str:
    lines = ["---"]
    if page_no is not None:
        lines.append(f"page: {page_no}")
    if print_page is not None:
        lines.append(f"print_page: {print_page}")
    lines.append(f"book: {book_id}")
    lines.append("tags:")
    for tag in tags:
        if tag:
            lines.append(f"  - {_yaml_escape(tag)}")
    lines.append("---")
    return "\n".join(lines)


def render_page_markdown(
    blocks: list[dict[str, Any]],
    *,
    book_id: str,
    page_no: int,
    page_count: int,
    print_page: int | None,
    tags: list[str],
) -> str:
    headers = _sort_chrome([b for b in blocks if str(b.get("type") or "").lower() in _HEADER_TYPES])
    footnotes = _sort_chrome([b for b in blocks if str(b.get("type") or "").lower() in _FOOTNOTE_TYPES])
    footers = _sort_chrome([b for b in blocks if str(b.get("type") or "").lower() in _FOOTER_TYPES])
    numbers = _sort_chrome([b for b in blocks if str(b.get("type") or "").lower() in _PAGE_NO_TYPES])
    body = [b for b in blocks if str(b.get("type") or "").lower() in _BODY_TYPES]
    parts: list[str] = [
        page_frontmatter(book_id=book_id, page_no=page_no, print_page=print_page, tags=tags)
    ]
    header_lines = [block_plain(b) for b in headers]
    header_block = _callout("header", header_lines)
    if header_block:
        parts.append(header_block)
    nav: list[str] = []
    if page_no > 1:
        nav.append(f"[[{page_no - 1:04d}]]")
    if page_no < page_count:
        nav.append(f"[[{page_no + 1:04d}]]")
    if nav:
        parts.append(" · ".join(nav))
    first_title = True
    for idx, block in enumerate(body, start=1):
        text = render_body_block(block, book_id)
        if not text:
            continue
        btype = str(block.get("type") or "").strip().lower()
        if first_title and btype in {"title", "heading", "page_title"}:
            text = f"{text} ^page-{page_no}"
            first_title = False
        elif btype in _BODY_TYPES:
            text = f"{text} ^kb-{btype}-{idx}"
        parts.append(text)
    fn_lines = [block_plain(b) for b in footnotes]
    fn_block = _callout("footnote", fn_lines)
    if fn_block:
        parts.append(fn_block)
    footer_block = _callout("footer", [block_plain(b) for b in footers])
    if footer_block:
        parts.append(footer_block)
    page_block = _callout("page", [block_plain(b) for b in numbers])
    if page_block:
        parts.append(page_block)
    if first_title:
        parts.append(f"^page-{page_no}")
    return "\n\n".join(parts).rstrip() + "\n"


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        match = re.match(r"^---\r?\n[\s\S]*?\r?\n---\r?\n?", text)
        if match:
            return text[match.end() :]
    return text
