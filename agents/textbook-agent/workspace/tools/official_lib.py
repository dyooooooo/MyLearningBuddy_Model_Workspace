"""Official MinerU markdown line.

Library only — not in manifest.yaml. No ``app.*`` imports, no ``v2md_*``.
Reads MinerU ``mineru/layout.json`` (middle.json ``pdf_info``) and, if that
file is missing, the official v1 content list (``page_idx``).
Does not write ``pages/``, ``mid/``, ``v2md/``, or ``mineru/full.md``.
Does not search-slice ``full.md``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_MD_IMG = re.compile(r"(!\[[^\]]*\]\()(images/[^)]+)(\))")
_HTML_IMG = re.compile(r"""(\bsrc=["'])images/([^"']+)(["'])""", re.I)
_HTML_SRC = re.compile(r"""(\bsrc=["'])([^"']+)(["'])""", re.I)
_CHROME = frozenset(
    {"header", "footer", "page_header", "page_footer", "page_number", "page-number", "page_footnote"}
)
_V1_BODY = frozenset(
    {
        "text",
        "title",
        "equation",
        "interline_equation",
        "table",
        "image",
        "chart",
        "ref_text",
        "aside_text",
        "code",
    }
)
_TEXT_TYPES = frozenset({"text", "list", "index", "abstract", "ref_text", "aside_text"})
_VISUAL_TYPES = frozenset({"image", "chart", "table", "code"})
_CAPTION_TYPES = frozenset(
    {
        "image_caption",
        "image_footnote",
        "table_caption",
        "table_footnote",
        "chart_caption",
        "chart_footnote",
        "code_caption",
        "code_footnote",
    }
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


def asset_url(book_id: str, path: str) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if not raw:
        return ""
    if raw.startswith("/api/") or raw.startswith("http://") or raw.startswith("https://"):
        return raw
    name = raw.rsplit("/", 1)[-1]
    if not name:
        return ""
    key = f"kb/pdfs/{safe_book_id(book_id)}/assets/mineru/{name}"
    return f"/api/v1/kb/proxy/asset?key={quote(key, safe='')}"


def rewrite_image_urls(markdown: str, book_id: str) -> str:
    if not markdown:
        return markdown

    def _md(match: re.Match[str]) -> str:
        url = asset_url(book_id, match.group(2))
        return match.group(0) if not url else f"{match.group(1)}{url}{match.group(3)}"

    def _html(match: re.Match[str]) -> str:
        url = asset_url(book_id, match.group(2))
        return match.group(0) if not url else f"{match.group(1)}{url}{match.group(3)}"

    return _HTML_IMG.sub(_html, _MD_IMG.sub(_md, markdown))


def find_v1_path(book: Path) -> Path | None:
    folder = book / "mineru"
    if not folder.is_dir():
        return None
    hits = [
        path
        for path in sorted(folder.glob("*_content_list.json"))
        if not path.name.endswith("_content_list_v2.json")
    ]
    return hits[0] if hits else None


def find_layout_path(book: Path) -> Path | None:
    path = book / "mineru" / "layout.json"
    return path if path.is_file() else None


def load_v1_pages(book: Path) -> dict[int, list[dict[str, Any]]]:
    path = find_v1_path(book)
    if path is None:
        raise FileNotFoundError(f"missing content_list under {book / 'mineru'}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("content_list is empty")
    grouped: dict[int, list[dict[str, Any]]] = {}
    for block in data:
        if not isinstance(block, dict):
            continue
        try:
            page_no = int(block.get("page_idx") or 0) + 1
        except (TypeError, ValueError):
            page_no = 1
        if page_no < 1:
            page_no = 1
        grouped.setdefault(page_no, []).append(block)
    return dict(sorted(grouped.items()))


def load_pdf_page(book: Path, page_no: int) -> dict[str, Any] | None:
    path = find_layout_path(book)
    if path is None or page_no < 1:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    info = data.get("pdf_info") if isinstance(data, dict) else None
    if not isinstance(info, list):
        return None
    want = page_no - 1
    for page in info:
        if not isinstance(page, dict):
            continue
        try:
            idx = int(page.get("page_idx"))
        except (TypeError, ValueError):
            continue
        if idx == want:
            return page
    return None


def _plain_field(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_plain_field(item) for item in value]
        return "".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "caption"):
            got = _plain_field(value.get(key))
            if got:
                return got
    return ""


def _img_name(path: object) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("data:"):
        return ""
    return raw.rsplit("/", 1)[-1]


def _is_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _index_order(blocks: list[Any]) -> list[dict[str, Any]]:
    numbered = [(i, block) for i, block in enumerate(blocks or []) if isinstance(block, dict)]
    numbered.sort(key=lambda item: (item[1].get("index", float("inf")), item[0]))
    return [block for _, block in numbered]


def _prefix_html_imgs(html: str) -> str:
    def _one(match: re.Match[str]) -> str:
        src = match.group(2)
        if src.startswith(("data:", "images/", "http://", "https://", "/")):
            return match.group(0)
        name = _img_name(src)
        return match.group(0) if not name else f"{match.group(1)}images/{name}{match.group(3)}"

    return _HTML_SRC.sub(_one, html)


def _render_span(span: dict[str, Any]) -> tuple[str, str] | None:
    kind = str(span.get("type") or "")
    content = str(span.get("content") or "").strip()
    if kind in {"inline_equation", "equation"}:
        return ("eq", f"${content}$") if content else None
    if kind == "interline_equation":
        if content:
            return ("ieq", f"\n$$\n{content}\n$$\n")
        name = _img_name(span.get("image_path"))
        return ("img", f"![](images/{name})") if name else None
    if kind == "text":
        return ("text", content) if content else None
    return None


def _para_text(block: dict[str, Any]) -> str:
    nested = block.get("blocks")
    if nested and not block.get("lines"):
        parts = [_para_text(child) for child in _index_order(nested)]
        return "\n\n".join(part for part in parts if part.strip())
    lines = block.get("lines") or []
    blob = "".join(
        str(span.get("content") or "")
        for line in lines
        for span in (line.get("spans") or [])
        if isinstance(span, dict)
    )
    cjk = _is_cjk(blob)
    parts: list[str] = []
    for line_i, line in enumerate(lines):
        if not isinstance(line, dict):
            continue
        rendered = [
            item
            for span in (line.get("spans") or [])
            if isinstance(span, dict)
            for item in (_render_span(span),)
            if item
        ]
        for i, (kind, text) in enumerate(rendered):
            if kind == "ieq":
                parts.append(text)
                continue
            parts.append(text)
            last = i == len(rendered) - 1
            if cjk:
                if not last:
                    parts.append(" ")
            elif not last or line_i < len(lines) - 1:
                parts.append(" ")
    return "".join(parts).strip()


def _visual_segments(block: dict[str, Any]) -> list[tuple[str, str]]:
    kind = str(block.get("type") or "")
    if kind in _CAPTION_TYPES:
        text = _para_text(block)
        return [(text, "md")] if text else []
    if kind in {"image_body", "chart_body"}:
        out: list[tuple[str, str]] = []
        for line in block.get("lines") or []:
            if not isinstance(line, dict):
                continue
            for span in line.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                name = _img_name(span.get("image_path"))
                if name:
                    out.append((f"![](images/{name})", "md"))
        return out
    if kind == "table_body":
        out = []
        for line in block.get("lines") or []:
            if not isinstance(line, dict):
                continue
            for span in line.get("spans") or []:
                if not isinstance(span, dict):
                    continue
                html = str(span.get("html") or "").strip()
                if html:
                    out.append((_prefix_html_imgs(html), "html"))
                    continue
                name = _img_name(span.get("image_path"))
                if name:
                    out.append((f"![](images/{name})", "md"))
        return out
    if kind == "code_body":
        text = _para_text(block)
        if not text:
            return []
        lang = str(block.get("guess_lang") or "txt") or "txt"
        return [(f"```{lang}\n{text}\n```", "md")]
    return []


def _visual_md(para: dict[str, Any]) -> str:
    chunks: list[str] = []
    prev = ""
    for child in _index_order(para.get("blocks") or []):
        for text, kind in _visual_segments(child):
            if chunks:
                if prev == "html":
                    chunks.append("\n\n")
                elif kind == "html":
                    chunks.append("\n")
                else:
                    chunks.append(" \n")
            chunks.append(text)
            prev = kind
    return "".join(chunks)


def make_from_para_blocks(blocks: list[Any]) -> str:
    """MinerU official page markdown from one ``pdf_info`` page's ``para_blocks``."""
    out: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        typ = str(block.get("type") or "")
        if typ in _TEXT_TYPES:
            text = _para_text(block)
        elif typ == "title":
            try:
                level = int(block.get("level") or 1)
            except (TypeError, ValueError):
                level = 1
            if level < 1:
                level = 0
            text = f"{'#' * level} {_para_text(block)}"
        elif typ == "interline_equation":
            text = _para_text(block)
        elif typ in _VISUAL_TYPES:
            text = _visual_md(block)
        else:
            text = _para_text(block)
        if text.strip():
            out.append(text.strip())
    return ("\n\n".join(out) + "\n") if out else ""


def make_from_v1_blocks(blocks: list[Any]) -> str:
    """Fallback: page-scoped v1 content list, chrome skipped. Not used when layout.json exists."""
    out: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "").lower()
        if kind in _CHROME or kind not in _V1_BODY:
            continue
        if kind in {"image", "chart"}:
            parts = []
            name = _img_name(block.get("img_path"))
            if name:
                parts.append(f"![](images/{name})")
            cap = _plain_field(block.get("image_caption") or block.get("chart_caption"))
            if cap:
                parts.append(cap)
            text = "\n".join(parts)
        elif kind == "table":
            parts = []
            cap = _plain_field(block.get("table_caption"))
            if cap:
                parts.append(cap)
            html = str(block.get("table_body") or "").strip()
            if html:
                parts.append(_prefix_html_imgs(html))
            else:
                name = _img_name(block.get("img_path"))
                if name:
                    parts.append(f"![](images/{name})")
            text = "\n\n".join(parts)
        elif kind == "code":
            text = _plain_field(block.get("code_body") or block.get("text"))
        elif kind == "title":
            body = _plain_field(block.get("text"))
            text = f"# {body}" if body else ""
        elif kind in {"equation", "interline_equation"}:
            body = _plain_field(block.get("text"))
            text = f"$$\n{body}\n$$" if body and "$$" not in body else body
        else:
            text = _plain_field(block.get("text"))
        if text.strip():
            out.append(text.strip())
    return ("\n\n".join(out) + "\n") if out else ""


def render_official_page(book: Path, page_no: int) -> tuple[str, str]:
    """Return ``(markdown, via)`` for one 1-based printed page. ``via`` is layout or v1."""
    if find_layout_path(book) is not None:
        page = load_pdf_page(book, page_no)
        if page is None:
            return "", "layout"
        return make_from_para_blocks(page.get("para_blocks") or []), "layout"
    try:
        pages = load_v1_pages(book)
    except (OSError, ValueError, FileNotFoundError):
        return "", "v1"
    return make_from_v1_blocks(pages.get(page_no) or []), "v1"
