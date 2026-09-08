"""目录皮肤（`<nav class="kb-page-toc">`）还原成明文行。

`surfaces/page-body/lib/tag.mjs::stripSkin` 的 Python 镜像：中间文件里的目录一律是
`<nav>` / `<ol>` 包起来的 HTML，回写官方 `pages/*.md` 时要拆回「标签 印张页」的明文行。
两边规则必须一致，否则 `write_toc_pages` 的缩水/暴涨护栏会拿错的行数去比。

归属清单见 doc/toc-code-ownership.md。本文件是库，不进 manifest.yaml。
"""

from __future__ import annotations

import re
from html import unescape

_TOC_HEAD = re.compile(
    r'<(?:div|p|h[1-6]|span) class="kb-page-toc-heading">([\s\S]*?)</(?:div|p|h[1-6]|span)>',
    re.I,
)
_TOC_NAV = re.compile(r'<nav class="kb-page-toc"[\s\S]*?</nav>', re.I)
_TOC_OL = re.compile(r'<ol\b[^>]*class="[^"]*kb-page-toc[\s\S]*?</ol>', re.I)
_TOC_LI = re.compile(r"<li\b[^>]*>([\s\S]*?)</li>", re.I)
_TOC_TITLE = re.compile(r'kb-page-toc-title">([\s\S]*?)<', re.I)
_TOC_PAGE = re.compile(r'kb-page-toc-page">([\s\S]*?)<', re.I)
_TAGS = re.compile(r"<[^>]+>")


def plain(html: str) -> str:
    return unescape(_TAGS.sub("", html or "")).strip()


def nav_rows(html: str) -> list[str]:
    """一个 `<nav>` / `<ol>` 里的每个 `<li>` 还原成一行「标签 印张页」。"""
    rows: list[str] = []
    for li in _TOC_LI.finditer(html or ""):
        title_m = _TOC_TITLE.search(li.group(1))
        page_m = _TOC_PAGE.search(li.group(1))
        label = plain(title_m.group(1)) if title_m else ""
        num = plain(page_m.group(1)) if page_m else ""
        if label:
            rows.append(f"{label} {num}".strip() if num else label)
    return rows


def has_toc_skin(md: str) -> bool:
    text = md or ""
    return "kb-page-toc" in text


def strip_toc_skin(md: str) -> str:
    """只拆目录部分，其它 kb-* 皮肤原样留给调用方处理。"""
    text = md or ""

    def _nav(match: re.Match[str]) -> str:
        return "\n".join(nav_rows(match.group(0)))

    text = _TOC_HEAD.sub(lambda m: plain(m.group(1)), text)
    text = _TOC_NAV.sub(_nav, text)
    text = _TOC_OL.sub(_nav, text)
    return text
