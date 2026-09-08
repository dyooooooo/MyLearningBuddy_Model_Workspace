"""Strip baked knowledge-base skin HTML back to official markdown.

目录皮肤（`kb-page-toc`）的拆解规则归 agent，在 `workspace/tools/toc_text.py`；
这里只保留非目录部分。归属清单见 doc/toc-code-ownership.md。
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any

from app.workspace_module import load

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"::: (kb-[\w-]+)\r?\n([\s\S]*?)\r?\n:::")
_OPENER = re.compile(r"::: kb-[\w-]+")
_FENCE_NAME_LINE = re.compile(
    r"^[ \t]*(?::: )?kb-(?:center|title|subtitle|author|section(?:-center)?|"
    r"verse(?:-center)?|date|list-item|lead|solution(?:-tail)?|page-toc|"
    r"table-wrap|section-icon|drama|stage|dash)[ \t]*$"
)
_CLOSER = re.compile(r"^[ \t]*:::[ \t]*$")
_LIST_ITEM = re.compile(r'<p class="kb-list-item">([\s\S]*?)</p>', re.I)
_SOL_TAIL = re.compile(r'<p class="kb-solution-tail">([\s\S]*?)</p>', re.I)
_SOL = re.compile(r'<p class="kb-solution">[\s\S]*?</p>', re.I)
_LEAD = re.compile(
    r'<p class="kb-lead">[\s\S]*?<span class="kb-lead-label">([\s\S]*?)</span>([\s\S]*?)</p>',
    re.I,
)
_CENTER_DIV = re.compile(r'<div class="kb-center">\s*([\s\S]*?)\s*</div>', re.I)
_TITLE_DIV = re.compile(
    r'<div class="kb-(?:title|author|subtitle|section(?:-center)?)">\s*([\s\S]*?)\s*</div>',
    re.I,
)
_VERSE_DIV = re.compile(
    r'<div class="kb-verse(?:-center)?">\s*([\s\S]*?)\s*</div>',
    re.I,
)
_DATE_DIV = re.compile(r'<div class="kb-date">\s*([\s\S]*?)\s*</div>', re.I)
_TABLE_WRAP = re.compile(r'<div class="kb-table-wrap">([\s\S]*?)</div>', re.I)
_TAGS = re.compile(r"<[^>]+>")


def has_kb_fences(md: str) -> bool:
    return bool(re.search(r"::: kb-[\w-]+", md or ""))


def source_is_polluted(md: str) -> bool:
    try:
        mod = load("source_text", require=("source_is_polluted",))
        return bool(mod.source_is_polluted(md))
    except ImportError:
        raw = md or ""
        if _FENCE.search(raw) or _OPENER.search(raw):
            return True
        return any(_CLOSER.match(line) or _FENCE_NAME_LINE.match(line) for line in raw.splitlines())


def looks_like_skin(md: str) -> bool:
    text = md or ""
    return 'class="kb-' in text or "kb-page-toc" in text or source_is_polluted(text)


def _plain(html: str) -> str:
    return unescape(_TAGS.sub("", html or "")).strip()


_TOC_REQUIRE = ("strip_toc_skin", "nav_rows", "has_toc_skin")


def _toc_text() -> Any | None:
    try:
        return load("toc_text", require=_TOC_REQUIRE)
    except ImportError:
        logger.warning("toc_text missing; leaving kb-page-toc skin in place", exc_info=True)
        return None


def strip_skin(md: str) -> str:
    text = md or ""
    toc = _toc_text()
    if toc is not None:
        text = toc.strip_toc_skin(text)

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
    text = _CENTER_DIV.sub(lambda m: m.group(1).strip(), text)
    text = _TITLE_DIV.sub(lambda m: m.group(1).strip(), text)
    text = _VERSE_DIV.sub(lambda m: m.group(1).strip(), text)
    text = _DATE_DIV.sub(lambda m: m.group(1).strip(), text)
    text = _TABLE_WRAP.sub(lambda m: m.group(1).strip(), text)
    return _sanitize_fences(text)


def _fallback_sanitize(text: str) -> str:
    out = text or ""
    prev = None
    while prev != out:
        prev = out
        out = _FENCE.sub(lambda m: (m.group(2) or "").strip(), out)
    lines: list[str] = []
    for line in out.splitlines():
        if _CLOSER.match(line) or _FENCE_NAME_LINE.match(line):
            continue
        cleaned = re.sub(
            r"(?:^|(?<=\s))(kb-(?:center|title|subtitle|author|section(?:-center)?|"
            r"verse(?:-center)?|date|list-item|lead|solution(?:-tail)?|page-toc|"
            r"table-wrap|section-icon|drama|stage|dash))(?=\s|[^\w-]|$)",
            "",
            _OPENER.sub("", line),
        )
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).rstrip()
        if not cleaned.strip() and (_OPENER.search(line) or "kb-" in line):
            continue
        lines.append(cleaned)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return body.strip() + ("\n" if body.strip() else "")


def _sanitize_fences(text: str) -> str:
    try:
        mod = load("source_text", require=("sanitize_source",))
        return str(mod.sanitize_source(text))
    except ImportError:
        return _fallback_sanitize(text)


def peel_mid_remnants(md: str) -> str:
    try:
        mod = load("source_text", require=("sanitize_mid",))
        return str(mod.sanitize_mid(md))
    except ImportError:
        return md or ""
