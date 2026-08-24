"""Polish MinerU table HTML the way the textbook studio does, without sharing it."""

from __future__ import annotations

import re
from html import escape

from app.kb.assets import rewrite_local_image_paths
from app.kb.math_text import wrap_bare_math

_CELL = re.compile(r"(<t[dh]\b[^>]*>)([\s\S]*?)(</t[dh]>)", re.IGNORECASE)

_TABLE_RE = re.compile(r"<table\b[\s\S]*?</table>", re.IGNORECASE)
_FIRST_ROW = re.compile(r"<tr\b[^>]*>[\s\S]*?</tr>", re.IGNORECASE)
_TD = re.compile(r"<td\b", re.IGNORECASE)
_TH = re.compile(r"<th\b", re.IGNORECASE)


def _promote_header(html: str) -> str:
    if _TH.search(html):
        return html
    match = _FIRST_ROW.search(html)
    if not match:
        return html
    row = match.group(0)
    promoted = re.sub(r"<td\b", "<th", row, flags=re.IGNORECASE)
    promoted = re.sub(r"</td>", "</th>", promoted, flags=re.IGNORECASE)
    return html[: match.start()] + promoted + html[match.end() :]


def _column_count(html: str) -> int:
    match = _FIRST_ROW.search(html)
    if not match:
        return 0
    return len(re.findall(r"<t[dh]\b", match.group(0), flags=re.IGNORECASE))


def _wrap_cells(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        inner = match.group(2)
        if re.search(r"<img\b", inner, re.IGNORECASE):
            return match.group(0)
        return f"{match.group(1)}{wrap_bare_math(inner)}{match.group(3)}"

    return _CELL.sub(repl, html)


def _inject_caption(html: str, caption: str) -> str:
    if not caption or re.search(r"<caption\b", html, re.IGNORECASE):
        return html
    return re.sub(
        r"(<table\b[^>]*>)",
        rf"\1<caption>{escape(caption)}</caption>",
        html,
        count=1,
        flags=re.IGNORECASE,
    )


def _add_table_class(html: str, extra: str) -> str:
    if not extra:
        return html

    def repl(match: re.Match[str]) -> str:
        tag = match.group(0)
        if "class=" in tag.lower():
            return re.sub(
                r'class=(["\'])([^"\']*)\1',
                lambda m: f'class={m.group(1)}{m.group(2)} {extra}{m.group(1)}',
                tag,
                count=1,
                flags=re.IGNORECASE,
            )
        return tag[:-1] + f' class="{extra}">'

    return re.sub(r"<table\b[^>]*>", repl, html, count=1, flags=re.IGNORECASE)


def polish_table_html(
    html: str,
    *,
    caption: str = "",
    footnote: str = "",
    url_map: dict[str, str] | None = None,
) -> str:
    raw = (html or "").strip()
    if not raw:
        return ""
    match = _TABLE_RE.search(raw)
    table = match.group(0) if match else raw
    if url_map:
        table = rewrite_local_image_paths(table, url_map)
    table = _promote_header(table)
    table = _wrap_cells(table)
    table = _inject_caption(table, caption.strip())
    if _column_count(table) >= 5:
        table = _add_table_class(table, "kb-table--fit")
    foot = f'<p class="kb-table-foot">{escape(footnote.strip())}</p>' if footnote.strip() else ""
    if 'class="kb-table-wrap"' in raw:
        return table + foot
    return f'<div class="kb-table-wrap">{table}</div>{foot}'


def rewrite_html_images(html: str, url_map: dict[str, str]) -> str:
    return rewrite_local_image_paths(html, url_map)
