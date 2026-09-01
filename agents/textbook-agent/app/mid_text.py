"""Strip baked knowledge-base skin HTML back to official markdown."""

from __future__ import annotations

import re
from html import unescape

_FENCE = re.compile(r"::: (kb-[\w-]+)\r?\n([\s\S]*?)\r?\n:::")
_LIST_ITEM = re.compile(r'<p class="kb-list-item">([\s\S]*?)</p>', re.I)
_SOL_TAIL = re.compile(r'<p class="kb-solution-tail">([\s\S]*?)</p>', re.I)
_SOL = re.compile(r'<p class="kb-solution">[\s\S]*?</p>', re.I)
_LEAD = re.compile(
    r'<p class="kb-lead">[\s\S]*?<span class="kb-lead-label">([\s\S]*?)</span>([\s\S]*?)</p>',
    re.I,
)
_TOC_HEAD = re.compile(
    r'<(?:div|p|h[1-6]|span) class="kb-page-toc-heading">([\s\S]*?)</(?:div|p|h[1-6]|span)>',
    re.I,
)
_TOC_NAV = re.compile(r'<nav class="kb-page-toc"[\s\S]*?</nav>', re.I)
_TOC_LI = re.compile(r"<li\b[^>]*>([\s\S]*?)</li>", re.I)
_TOC_TITLE = re.compile(r'kb-page-toc-title">([\s\S]*?)<', re.I)
_TOC_PAGE = re.compile(r'kb-page-toc-page">([\s\S]*?)<', re.I)
_CENTER_DIV = re.compile(r'<div class="kb-center">\s*([\s\S]*?)\s*</div>', re.I)
_TABLE_WRAP = re.compile(r'<div class="kb-table-wrap">([\s\S]*?)</div>', re.I)
_TAGS = re.compile(r"<[^>]+>")


def has_kb_fences(md: str) -> bool:
    return bool(re.search(r"::: kb-[\w-]+", md or ""))


def looks_like_skin(md: str) -> bool:
    text = md or ""
    return 'class="kb-' in text or "<nav class=\"kb-page-toc\"" in text


def _plain(html: str) -> str:
    return unescape(_TAGS.sub("", html or "")).strip()


def strip_skin(md: str) -> str:
    text = md or ""

    def _list(match: re.Match[str]) -> str:
        return _plain(match.group(1))

    text = _LIST_ITEM.sub(_list, text)
    text = _SOL_TAIL.sub(_list, text)
    text = _SOL.sub("解", text)

    def _lead(match: re.Match[str]) -> str:
        label = _plain(match.group(1))
        rest = _plain(match.group(2))
        return f"{label} {rest}".strip()

    text = _LEAD.sub(_lead, text)
    text = _TOC_HEAD.sub(lambda m: _plain(m.group(1)), text)

    def _nav(match: re.Match[str]) -> str:
        rows: list[str] = []
        for li in _TOC_LI.finditer(match.group(0)):
            title_m = _TOC_TITLE.search(li.group(1))
            page_m = _TOC_PAGE.search(li.group(1))
            label = _plain(title_m.group(1)) if title_m else ""
            num = _plain(page_m.group(1)) if page_m else ""
            if label:
                rows.append(f"{label} {num}".strip() if num else label)
        return "\n".join(rows)

    text = _TOC_NAV.sub(_nav, text)
    text = _CENTER_DIV.sub(lambda m: m.group(1).strip(), text)
    text = _TABLE_WRAP.sub(lambda m: m.group(1).strip(), text)
    text = _FENCE.sub(lambda m: m.group(2).strip(), text)
    return text
