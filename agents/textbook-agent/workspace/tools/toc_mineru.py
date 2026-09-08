"""Harvest MinerU catalog candidates and judge whether it really found a TOC.

Library only — not in manifest.yaml. Reads textbooks/<id>/mineru/*content_list*.json
(v1 and v2). Does not call buddy-api extract_toc.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TOC_TITLES = {"目录", "目 录", "CONTENTS", "Contents", "contents"}
_TRAILING_PAGE = re.compile(
    r"^(?P<label>.+?)(?:\s+|\s*/\s*|\s*…+\s*|\s*\.{2,}\s*)(?P<page>\d{1,4})$"
)
_GLUE_JIE = re.compile(r"第\s*[一二三四五六七八九十百零\d]+\s*节")
_PAGE_NUMS = re.compile(r"(?:^|\s)(\d{1,3})(?=\s|$)")
_MAX_LABEL = 40
# 「第 章」缺章号不算目录行；要有章/节/单元/讲的序号。
_UNIT_LABEL = re.compile(r"第\s*[一二三四五六七八九十百零〇\d]+\s*[章节单元讲]")


def mineru_dir(book: Path) -> Path:
    return book / "mineru"


def load_mineru_blocks(book: Path) -> list[dict[str, Any]]:
    folder = mineru_dir(book)
    if not folder.is_dir():
        return []
    v2 = sorted(folder.glob("*_content_list_v2.json"))
    v1 = [
        path
        for path in sorted(folder.glob("*content_list*.json"))
        if not path.name.endswith("_content_list_v2.json")
    ]
    for path in v1 + v2:
        blocks = _load_list(path)
        if blocks:
            return blocks
    blocks_path = folder / "blocks.json"
    if blocks_path.is_file():
        return _load_list(blocks_path)
    return []


def _load_list(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list) and data and isinstance(data[0], list):
        flat: list[dict[str, Any]] = []
        for page_idx, page_blocks in enumerate(data):
            if not isinstance(page_blocks, list):
                continue
            for item in page_blocks:
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                row.setdefault("page_idx", page_idx)
                flat.append(row)
        return flat
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "".join(part for part in (_as_text(x) for x in value) if part)
    if isinstance(value, dict):
        for key in (
            "text",
            "content",
            "title_content",
            "paragraph_content",
            "item_content",
        ):
            if key in value:
                got = _as_text(value[key])
                if got:
                    return got
        return ""
    return ""


def block_text(block: dict[str, Any]) -> str:
    text = _as_text(block.get("text"))
    if text:
        return text
    return _as_text(block.get("content"))


def block_pdf_page(block: dict[str, Any]) -> int:
    raw = block.get("page_idx")
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        return 1
    return idx + 1 if idx >= 0 else 1


def is_index_block(block: dict[str, Any]) -> bool:
    btype = str(block.get("type") or "").strip().lower()
    if btype == "index":
        return True
    content = block.get("content")
    if btype == "list" and isinstance(content, dict):
        return str(content.get("list_type") or "") == "text_list"
    return False


def list_lines(block: dict[str, Any]) -> list[str]:
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
    blob = block_text(block)
    return [ln.strip() for ln in blob.splitlines() if ln.strip()]


def is_glued_catalog(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) >= 2:
        return any(_GLUE_JIE.findall(ln) and len(_GLUE_JIE.findall(ln)) >= 2 for ln in lines)
    jie_hits = len(_GLUE_JIE.findall(raw))
    if jie_hits >= 2:
        return True
    pages = _PAGE_NUMS.findall(raw)
    return jie_hits >= 1 and len(raw) > 60 and len(pages) >= 3


def parse_toc_line(raw_text: str) -> dict[str, Any] | None:
    text = (raw_text or "").strip()
    text = re.sub(r"^#{1,6}\s*", "", text)
    if not text or text in _TOC_TITLES:
        return None
    if is_glued_catalog(text):
        return None
    match = _TRAILING_PAGE.match(text)
    if match:
        label = match.group("label").strip(" .\u3000．…·•")
        page = int(match.group("page"))
        if label and not label.isdigit() and len(label) <= _MAX_LABEL:
            is_chapter = bool(re.match(r"^第[一二三四五六七八九十百零\d]+章", label))
            return {
                "label": label,
                "print_page": page,
                "is_chapter": is_chapter,
                "level": 0 if is_chapter else 1,
            }
    if re.match(r"^第[一二三四五六七八九十百零\d]+章", text) and len(text) <= _MAX_LABEL:
        return {"label": text, "print_page": None, "is_chapter": True, "level": 0}
    return None


def harvest_mineru_catalog(book: Path) -> dict[str, Any]:
    blocks = load_mineru_blocks(book)
    toc_pages: list[int] = []
    entries: list[dict[str, Any]] = []
    glued_pages: list[int] = []
    index_hits = 0

    for block in blocks:
        pdf_page = block_pdf_page(block)
        text = block_text(block)
        if text in _TOC_TITLES:
            toc_pages.append(pdf_page)
        if is_glued_catalog(text):
            glued_pages.append(pdf_page)

    toc_set = set(toc_pages)
    seen: set[tuple[str, object]] = set()

    def add(row: dict[str, Any] | None) -> None:
        if not row:
            return
        key = (str(row.get("label") or ""), row.get("print_page"))
        if key in seen:
            return
        seen.add(key)
        entries.append(row)

    for block in blocks:
        pdf_page = block_pdf_page(block)
        if is_index_block(block):
            rows = [parse_toc_line(ln) for ln in list_lines(block)]
            kept = [row for row in rows if row]
            if kept:
                index_hits += len(kept)
                toc_pages.append(pdf_page)
                toc_set.add(pdf_page)
                for row in kept:
                    add(row)
            continue
        text = block_text(block)
        if text in _TOC_TITLES or is_glued_catalog(text):
            continue
        if toc_set and pdf_page not in toc_set and (pdf_page - 1) not in toc_set:
            continue
        row = parse_toc_line(text)
        if row:
            toc_pages.append(pdf_page)
            toc_set.add(pdf_page)
            add(row)

    quality = classify_mineru_quality(
        toc_pages=sorted(set(toc_pages)),
        entries=entries,
        glued_pages=sorted(set(glued_pages)),
        index_hits=index_hits,
    )
    return {
        "toc_pages": sorted(set(toc_pages)),
        "entries": entries,
        "glued_pages": sorted(set(glued_pages)),
        "index_hits": index_hits,
        "quality": quality,
    }


def usable_catalog_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """短标签 + 印张页 + 带章/节序号。『第 章』这种残行不算。"""
    out: list[dict[str, Any]] = []
    for row in entries:
        label = str(row.get("label") or "").strip()
        if not label or len(label) > _MAX_LABEL:
            continue
        if row.get("print_page") is None:
            continue
        if not _UNIT_LABEL.search(label):
            continue
        out.append(row)
    return out


def classify_mineru_quality(
    *,
    toc_pages: list[int],
    entries: list[dict[str, Any]],
    glued_pages: list[int],
    index_hits: int,
) -> str:
    usable = usable_catalog_rows(entries)
    if glued_pages:
        return "weak"
    if index_hits >= 2 and len(usable) >= 2:
        return "strong"
    if toc_pages and len(usable) >= 2:
        return "strong"
    return "weak"
