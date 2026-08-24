"""Wrap MinerU bare LaTeX so the knowledge-base viewer can typeset it.

MinerU VLM often emits mixed Chinese + raw TeX (``\\leqslant``, ``\\in``)
without ``$`` delimiters. The textbook studio splits those runs before
MathJax; this file does the same for knowledge-base markdown, without
touching the studio pipeline.
"""

from __future__ import annotations

import re

_CJK_SPLIT = re.compile(
    r"([\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff，。；：、！？（）《》""''…—]+)"
)
_BARE_CMD = re.compile(r"\\(?:[a-zA-Z]+|[{}\[\]])")
_UNICODE_MATH = re.compile(r"[√∞∪∩≤≥≠παβγθ∈∉∅]")
_LETTER_REL = re.compile(r"[a-zA-Z]\s*[=<>]")
_PROTECTED = re.compile(
    r"\$\$[\s\S]+?\$\$"
    r"|\$[^$\n]+?\$"
    r"|\\\[[\s\S]+?\\\]"
    r"|\\\([\s\S]+?\\\)"
    r"|!\[[^\]]*\]\([^)]+\)"
    r"|<div class=\"kb-table-wrap\"[\s\S]+?</div>"
    r"|<table[\s\S]+?</table>"
    r"|<img\b[^>]*>"
    r"|<nav[\s\S]+?</nav>"
    r"|<p class=\"kb-(?:list-item|solution|solution-tail|lead)\"[\s\S]+?</p>",
    re.IGNORECASE,
)
_DISPLAY_HINT = re.compile(r"\\begin\{|\\frac\{|\\sum(?![a-zA-Z])|\\int(?![a-zA-Z])")


def _is_math_run(part: str) -> bool:
    text = part.strip()
    if not text or "$" in text:
        return False
    if text.startswith(("http://", "https://", "/api/")):
        return False
    if _BARE_CMD.search(text):
        return True
    if _UNICODE_MATH.search(text) and re.search(r"[=<>+\-*/^_{}\\]|[a-zA-Z]", text):
        return True
    if _LETTER_REL.search(text) and re.search(r"[+\-*/^_{}\\]|\\", text):
        return True
    if re.search(r"[a-zA-Z]\s*[<>]=?\s*[-+]?\d", text):
        return True
    return False


def _wrap_run(part: str) -> str:
    text = part.strip()
    lead = part[: len(part) - len(part.lstrip())]
    tail = part[len(part.rstrip()) :]
    if _DISPLAY_HINT.search(text) or "\n" in text or len(text) > 80:
        return f"{lead}$${text}$${tail}"
    return f"{lead}${text}${tail}"


def _wrap_unprotected(text: str) -> str:
    if not text:
        return text
    return "".join(
        _wrap_run(part) if _is_math_run(part) else part
        for part in _CJK_SPLIT.split(text)
    )


def wrap_bare_math(text: str) -> str:
    """Idempotent: already-delimited math, tables, images, TOC HTML stay put."""
    raw = text or ""
    if not raw:
        return raw
    chunks: list[str] = []
    cursor = 0
    for match in _PROTECTED.finditer(raw):
        chunks.append(_wrap_unprotected(raw[cursor : match.start()]))
        chunks.append(match.group(0))
        cursor = match.end()
    chunks.append(_wrap_unprotected(raw[cursor:]))
    return "".join(chunks)
