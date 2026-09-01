"""Turn MinerU Cloud blocks into per-page markdown for the knowledge base."""

from __future__ import annotations

from typing import Any

from app.kb.math_text import wrap_bare_math
from app.kb.prose import looks_like_heading, polish_page_prose
from app.kb.table_html import polish_table_html
from app.kb.toc import is_index_block, looks_like_toc_blob

_SKIP_TYPES = frozenset(
    {
        "page_header",
        "header",
        "page_footer",
        "footer",
        "page_number",
        "page-number",
        "discarded",
    }
)
_TITLE_TYPES = frozenset({"title", "heading", "page_title"})
_EQUATION_TYPES = frozenset({"equation", "equation_interline"})
_FIGURE_TYPES = frozenset({"image", "chart"})


def group_blocks_by_page(blocks: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for block in blocks:
        if not isinstance(block, dict):
            continue
        raw = block.get("page_idx")
        try:
            idx = int(raw)
            page_no = idx + 1 if idx >= 0 else 1
        except (TypeError, ValueError):
            page_no = 1
        grouped.setdefault(page_no, []).append(block)
    return dict(sorted(grouped.items()))


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [_as_text(x) for x in value]
        return "".join(p for p in parts if p)
    if isinstance(value, dict):
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
        ):
            if key in value:
                got = _as_text(value[key])
                if got:
                    return got
        return ""
    return ""


def _plain_text(block: dict[str, Any]) -> str:
    text = _as_text(block.get("text"))
    if text:
        return text
    content = block.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if not isinstance(content, dict):
        return _as_text(content)
    for key in (
        "title_content",
        "paragraph_content",
        "math_content",
        "code_content",
        "algorithm_content",
        "page_aside_text_content",
    ):
        val = content.get(key)
        got = _as_text(val)
        if got:
            return got
    items = content.get("list_items")
    if isinstance(items, list):
        lines: list[str] = []
        for item in items:
            line = _as_text(item)
            if line:
                lines.append(line)
        if lines:
            return "\n".join(lines)
    return ""


def _caption(raw: object) -> str:
    return _as_text(raw)


def _image_path(block: dict[str, Any]) -> str:
    for key in ("img_path", "image_path", "src"):
        val = block.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    content = block.get("content")
    if isinstance(content, dict):
        for key in ("img_path", "image_path", "src", "path"):
            val = content.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        src = content.get("image_source")
        if isinstance(src, dict):
            for key in ("path", "img_path", "image_path", "src"):
                val = src.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        if isinstance(src, str) and src.strip():
            return src.strip()
    return ""


def _image_markdown(block: dict[str, Any], *, alt_default: str = "插图") -> str:
    path = _image_path(block)
    if not path:
        return ""
    alt_bits: list[str] = []
    content = block.get("content") if isinstance(block.get("content"), dict) else {}
    for key in ("image_caption", "table_caption", "caption", "image_footnote", "table_footnote"):
        cap = _caption(block.get(key) or content.get(key))
        if cap:
            alt_bits.append(cap)
    alt = " · ".join(alt_bits) or alt_default
    md = f"![{alt}]({path})"
    if alt_bits:
        return f"{md}\n\n*{alt_bits[0]}*"
    return md


def _toc_plain(lines: list[str]) -> str:
    out: list[str] = []
    for line in lines:
        raw = (line or "").strip().lstrip("-•·* ").strip()
        if raw:
            out.append(raw)
    return "\n".join(out)


def _block_lines(block: dict[str, Any], text: str) -> list[str]:
    content = block.get("content")
    items: list[object] = []
    if isinstance(content, dict):
        raw = content.get("list_items") or content.get("items")
        if isinstance(raw, list):
            items = raw
    if not items:
        raw = block.get("list_items") or block.get("items")
        if isinstance(raw, list):
            items = raw
    if items:
        return [ln for ln in (_as_text(x) for x in items) if ln]
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _is_toc_block(block: dict[str, Any], text: str) -> bool:
    if is_index_block(block):
        return True
    return looks_like_toc_blob(text)


def _equation_markdown(block: dict[str, Any]) -> str:
    latex = _plain_text(block)
    if latex:
        inner = latex.strip()
        if inner.startswith("$$") and inner.endswith("$$"):
            return inner
        if inner.startswith("$") and inner.endswith("$") and "\n" not in inner:
            inner = inner[1:-1].strip()
        return f"$$\n{inner}\n$$"
    return _image_markdown(block, alt_default="公式")


def _render_block(
    block: dict[str, Any],
    print_to_pdf: dict[int, int] | None,
    *,
    skip_chrome: bool = True,
) -> str:
    btype = str(block.get("type") or "").strip().lower()
    if skip_chrome and btype in _SKIP_TYPES:
        return ""
    if btype == "discarded":
        return ""
    text = _plain_text(block)
    if _is_toc_block(block, text):
        return _toc_plain(_block_lines(block, text))
    if btype in _EQUATION_TYPES:
        return _equation_markdown(block)
    if btype in _FIGURE_TYPES:
        return _image_markdown(block)
    if btype == "table":
        content = block.get("content") if isinstance(block.get("content"), dict) else {}
        cap = _caption(
            block.get("table_caption")
            or content.get("table_caption")
            or block.get("caption")
            or content.get("caption")
        )
        foot = _caption(block.get("table_footnote") or content.get("table_footnote"))
        table = (
            block.get("table_body")
            or block.get("html")
            or block.get("table_html")
            or content.get("table_body")
            or content.get("html")
            or content.get("table_html")
            or ""
        )
        if isinstance(table, str) and table.strip().startswith("<"):
            return polish_table_html(table, caption=cap, footnote=foot, wrap=False)
        if _image_path(block):
            return _image_markdown(block, alt_default=cap or "表格")
        return cap
    if btype in _TITLE_TYPES and text:
        hashes = "##" if len(text) < 40 else "###"
        return f"{hashes} {text}"
    stripped = text.strip() if text else ""
    if stripped in {"#", "第"} or (stripped and set(stripped) <= {"#"}):
        return ""
    if text and looks_like_heading(text):
        return f"### {text.strip()}"
    return text


def _join_parts(parts: list[str]) -> str:
    return wrap_bare_math(polish_page_prose("\n\n".join(p for p in parts if p)))


def blocks_to_page_markdown(
    blocks: list[dict[str, Any]],
    *,
    print_to_pdf: dict[int, int] | None = None,
) -> str:
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        piece = _render_block(block, print_to_pdf)
        if piece:
            parts.append(piece)
    return _join_parts(parts)

