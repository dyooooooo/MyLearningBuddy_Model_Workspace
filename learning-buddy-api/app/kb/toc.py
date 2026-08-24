"""Build a clickable book outline from MinerU Cloud content_list blocks."""

from __future__ import annotations

import re
from typing import Any

_TRAILING_PAGE = re.compile(
    r"^(?P<label>.+?)\s+(?P<page>\d{1,4})$"
)
_CHAPTER = re.compile(r"^第[一二三四五六七八九十百零\d]+章")
_SECTION = re.compile(r"^\*?\d+\.\d+")
_PAGE_ONLY = re.compile(r"^\d{1,4}$")
_TOC_TITLES = {"目录", "目 录", "CONTENTS", "Contents"}
_MATHY = ("=", "\\", "^", "leqslant", "geqslant", "不等式化为", "定理 ", "已知函数")


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_as_text(x) for x in value]
        return "".join(p for p in parts if p)
    if isinstance(value, dict):
        for key in ("text", "content", "item_content", "title", "paragraph_content"):
            if key in value:
                t = _as_text(value[key])
                if t:
                    return t
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _block_type(block: dict[str, Any]) -> str:
    return str(block.get("type") or "").strip().lower()


def _block_text(block: dict[str, Any]) -> str:
    text = _as_text(block.get("text"))
    if text:
        return text
    return _as_text(block.get("content"))


def _list_lines(block: dict[str, Any]) -> list[str]:
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
    lines = [_as_text(item) for item in items]
    if lines:
        return [ln for ln in lines if ln]
    blob = _block_text(block)
    return _split_lines(blob)


def _split_lines(text: str) -> list[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\s*\|\s*", "\n", raw)
    out: list[str] = []
    for line in raw.splitlines():
        line = line.strip().lstrip("-•·* ").strip()
        if line:
            out.append(line)
    return out


def parse_entry(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if text.startswith("\\*"):
        text = "*" + text[2:]
    match = _TRAILING_PAGE.match(text)
    if match:
        label = match.group("label").strip(" .\u3000．…·•")
        page = int(match.group("page"))
        if label and not label.isdigit():
            return {
                "label": label,
                "print_page": page,
                "is_chapter": bool(_CHAPTER.match(label)),
                "level": _level(label),
            }
    return {
        "label": text,
        "print_page": None,
        "is_chapter": bool(_CHAPTER.match(text)),
        "level": _level(text),
    }


def _level(label: str) -> int:
    if _CHAPTER.match(label):
        return 0
    if re.match(r"^\*?\d+\.\d+\.\d+\b", label):
        return 2
    if _SECTION.match(label):
        return 1
    return 1


def _looks_like_entry(line: str) -> bool:
    s = (line or "").strip()
    if not s or s in _TOC_TITLES:
        return False
    if any(tok in s for tok in _MATHY) or s.startswith(("(", "当", "解")):
        return False
    if _CHAPTER.match(s) or _SECTION.match(s):
        return True
    ent = parse_entry(s)
    return bool(ent.get("print_page") is not None and ent.get("label"))


def looks_like_toc_blob(text: str) -> bool:
    lines = _split_lines(text)
    if not lines:
        return False
    hits = [ln for ln in lines if _looks_like_entry(ln)]
    if len(lines) == 1:
        return bool(parse_entry(lines[0]).get("print_page"))
    if len(hits) < 2:
        return False
    return (len(hits) / len(lines)) >= 0.55


def _pdf_page(block: dict[str, Any]) -> int:
    raw = block.get("page_idx")
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        return 1
    return idx + 1 if idx >= 0 else 1


def is_index_block(block: dict[str, Any]) -> bool:
    btype = _block_type(block)
    if btype == "index":
        return True
    if btype == "list":
        content = block.get("content")
        if isinstance(content, dict) and str(content.get("list_type") or "") == "text_list":
            return True
    return False


_FRAGMENT = re.compile(r"^[a-zA-Z0-9]\s*[-+=.]$")


def keep_entry(item: dict[str, Any]) -> bool:
    label = str(item.get("label") or "").strip()
    if not label or len(label) > 40:
        return False
    if _FRAGMENT.match(label) or len(label) < 2:
        return False
    if any(tok in label for tok in _MATHY):
        return False
    if label.startswith(("(", "当", "解")):
        return False
    if _CHAPTER.match(label) or _SECTION.match(label) or item.get("is_chapter"):
        return True
    if label in {"内容提要", "复习题"}:
        return True
    return item.get("print_page") is not None and bool(label)


def _is_page_number_block(block: dict[str, Any]) -> bool:
    btype = _block_type(block)
    if btype in {"page_number", "page-number"}:
        return True
    text = _block_text(block)
    return btype in {"discarded", "header", "footer"} and bool(_PAGE_ONLY.match(text))


def extract_toc(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    toc_pages: list[int] = []
    print_to_pdf: dict[str, int] = {}

    for block in blocks:
        if not isinstance(block, dict):
            continue
        pdf_page = _pdf_page(block)
        if _is_page_number_block(block):
            digits = _block_text(block)
            if _PAGE_ONLY.match(digits):
                print_to_pdf[digits] = pdf_page
        if _block_text(block) in _TOC_TITLES:
            toc_pages.append(pdf_page)

    toc_page_set = set(toc_pages)
    for block in blocks:
        if not isinstance(block, dict):
            continue
        pdf_page = _pdf_page(block)
        text = _block_text(block)
        collected: list[dict[str, Any]] = []
        if is_index_block(block):
            collected = [parse_entry(ln) for ln in _list_lines(block) if ln not in _TOC_TITLES]
        elif looks_like_toc_blob(text) and (
            pdf_page in toc_page_set or (pdf_page - 1) in toc_page_set
        ):
            collected = [
                parse_entry(ln)
                for ln in _split_lines(text)
                if ln not in _TOC_TITLES and (_looks_like_entry(ln) or _CHAPTER.match(ln))
            ]
        if collected:
            toc_pages.append(pdf_page)
            toc_page_set.add(pdf_page)
            for item in collected:
                if not keep_entry(item):
                    continue
                row = dict(item)
                # Never pin a chapter to the outline sheet itself.
                row["pdf_page"] = None
                entries.append(row)

    seen: set[tuple[str, object]] = set()
    unique: list[dict[str, Any]] = []
    for item in entries:
        key = (str(item.get("label") or ""), item.get("print_page"))
        if key in seen or not keep_entry(item):
            continue
        seen.add(key)
        unique.append(item)

    for item in unique:
        print_page = item.get("print_page")
        if print_page is None:
            continue
        mapped = print_to_pdf.get(str(print_page))
        if mapped:
            item["pdf_page"] = mapped

    return {
        "entries": unique,
        "print_to_pdf": {int(k): v for k, v in print_to_pdf.items() if str(k).isdigit()},
        "toc_pages": sorted(set(toc_pages)),
    }


def _line_matches_label(line: str, label: str) -> bool:
    text = (line or "").strip().lstrip("#").strip()
    if not text or len(text) > max(len(label) + 12, 24):
        return False
    if text == label or text.startswith(label):
        return True
    chapter = _CHAPTER.match(label)
    if chapter:
        head = chapter.group(0)
        rest = _CHAPTER.sub("", label).strip()
        return text.startswith(head) or bool(rest) and (text == rest or text.startswith(rest))
    section = re.match(r"^(\*?\d+\.\d+(?:\.\d+)?)", label)
    if section:
        return bool(re.match(rf"^{re.escape(section.group(1))}(?:\s|$)", text))
    return False


def _find_heading_page(
    label: str,
    page_texts: dict[int, str],
    *,
    start: int,
    skip: set[int],
) -> int | None:
    for page_no in sorted(n for n in page_texts if n >= start and n not in skip):
        lines = [ln.strip() for ln in (page_texts.get(page_no) or "").splitlines() if ln.strip()]
        if not lines:
            continue
        if lines[0] in {"内容提要", "复习题"} and _CHAPTER.match(label):
            continue
        if any(_line_matches_label(ln, label) for ln in lines[:4]):
            return page_no
    return None


def refine_toc(toc: dict[str, Any], page_texts: dict[int, str]) -> dict[str, Any]:
    """Map outline labels onto PDF pages using the page markdown already stored."""
    skip = {int(p) for p in (toc.get("toc_pages") or [])}
    print_to_pdf = {int(k): int(v) for k, v in (toc.get("print_to_pdf") or {}).items()}
    entries: list[dict[str, Any]] = []
    cursor = 1
    for raw in toc.get("entries") or []:
        if not isinstance(raw, dict) or not keep_entry(raw):
            continue
        item = dict(raw)
        item["pdf_page"] = None
        label = str(item.get("label") or "")
        is_extra = label in {"内容提要", "复习题"}
        found = _find_heading_page(label, page_texts, start=cursor, skip=skip)
        if found:
            item["pdf_page"] = found
            if not is_extra:
                cursor = found
            print_page = item.get("print_page")
            if print_page is not None:
                print_to_pdf[int(print_page)] = found
        elif item.get("print_page") is not None:
            mapped = print_to_pdf.get(int(item["print_page"]))
            if mapped:
                item["pdf_page"] = mapped
        entries.append(item)

    last_section = 0
    for item in reversed(entries):
        if item.get("is_chapter"):
            pinned = item.get("pdf_page")
            if last_section and (not pinned or int(pinned) in skip):
                item["pdf_page"] = last_section
            last_section = 0
            continue
        if item.get("pdf_page"):
            last_section = int(item["pdf_page"])

    return {
        "entries": entries,
        "print_to_pdf": print_to_pdf,
        "toc_pages": sorted(skip),
    }
