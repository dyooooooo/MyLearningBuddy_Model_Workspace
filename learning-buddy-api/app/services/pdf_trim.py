from __future__ import annotations

import re

from app.services.pdf_extract import PdfExtractResult, PdfPageText

_CHAPTER_RE = re.compile(r"第[０-９0-9一二三四五六七八九十百千]+章")
_LESSON_RE = re.compile(r"^[０-９0-9]+[\.．][０-９0-9]+\s+\S")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = len(_CJK_RE.findall(text))
    return cjk / max(len(text), 1)


def detect_trim_from_page(pages: list[PdfPageText]) -> int:
    """Return 1-based page index where main body starts."""
    if not pages:
        return 1

    for page in pages:
        text = page.text.strip()
        if not text:
            continue
        if "目录" in text[:80] and "1.1" not in text and "１．１" not in text:
            continue
        m = _CHAPTER_RE.search(text)
        if m:
            tail = text[m.start() :][:400]
            has_lesson = bool(_LESSON_RE.search(tail))
            if has_lesson or (_cjk_ratio(tail) >= 0.25 and len(tail) > 80):
                return page.page_no
            if page.page_no > 1 and len(text.strip()) > 15 and "目录" not in text[:40]:
                return page.page_no

    for page in pages:
        for line in page.text.splitlines():
            s = line.strip()
            if _LESSON_RE.match(s) and _cjk_ratio(s) >= 0.3:
                return page.page_no

    fallback = min(10, max(1, len(pages) // 10))
    return fallback


def trim_extract_result(result: PdfExtractResult) -> tuple[str, dict]:
    """Drop front matter pages; return trimmed markdown + trim meta."""
    trim_from = detect_trim_from_page(result.pages)
    front_pages = [p for p in result.pages if p.page_no < trim_from]
    body_pages = [p for p in result.pages if p.page_no >= trim_from]

    front_preview = "\n".join(p.text[:200] for p in front_pages[:3]).strip()[:500]
    body_text = "\n\n".join(p.text for p in body_pages if p.text.strip())

    meta = {
        **result.meta,
        "trim_from_page": trim_from,
        "front_matter_preview": front_preview,
        "char_count": len(body_text),
    }
    return body_text, meta
