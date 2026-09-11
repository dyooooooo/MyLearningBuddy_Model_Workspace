"""New-line TOC processor: derive a book outline from the canonical IR.

Detects TOC pages (a 目录/目 录/Contents title over a dot-leader text
block), parses ``标题 …… 页码`` lines into leveled entries, and maps print
pages to pdf pages via the per-page page-number chrome. Writes
``textbooks/<id>/ir/toc.json`` on apply=fix; scan only reports. Existing
human ``toc.json`` stays untouched and serves as the diff reference.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ir_lib import _join_text, book_dir, load_layout, safe_book_id

_TOC_TITLES = {"目录", "目 录", "目  录", "contents", "contens", "目 録"}
_LEADER = re.compile(r"^(?P<title>.+?)[\s　]*[.．·•‥…⋯]{2,}[\s　]*(?P<page>\d{1,3}|[IVXLC]+)$", re.I)
_LEVEL_PATTERNS = [
    (re.compile(r"^第[0-9０-９一二三四五六七八九十百]+[章单元课篇]"), 0),
    (re.compile(r"^(附录|后记|序言|前言|绪言|introduction|appendix)", re.I), 0),
    (re.compile(r"^[0-9０-９]+\.[0-9０-９]+\.[0-9０-９]+"), 2),
    (re.compile(r"^[0-9０-９]+\.[0-9０-９]+"), 1),
    (re.compile(r"^unit\s+[0-9]+", re.I), 0),
    (re.compile(r"^[0-9０-９]+\s"), 1),
]


def _level(title: str) -> int:
    head = title.strip()
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.match(head):
            return level
    return 1


def _norm_num(raw: str) -> str:
    return raw.strip().lstrip("0") or "0"


def _toc_page_numbers(book: Path, skip_pages: set[int]) -> dict[str, int]:
    """印刷页码 → pdf 页：逐页取 discarded 页码块的首个数字串。

    目录页自身页脚/版面里的数字会污染映射（绪言=1 被目录页抢走），
    检出目录页后把它们从建图范围里排除。
    """
    layout = load_layout(book) or {}
    out: dict[str, int] = {}
    for page in layout.get("pdf_info", []) or []:
        if not isinstance(page, dict):
            continue
        idx = int(page.get("page_idx") or 0) + 1
        if idx in skip_pages:
            continue
        for block in page.get("discarded_blocks", []) or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "") not in ("page_number", "page-number"):
                continue
            text = _join_text(block.get("lines"))
            m = re.search(r"\d{1,3}", text)
            if m:
                out.setdefault(_norm_num(m.group(0)), idx)
    return out


def build_toc(book: Path, front_pages: int = 12) -> dict[str, Any]:
    layout = load_layout(book)
    if not layout:
        raise FileNotFoundError(f"missing layout.json under {book / 'mineru'}")
    entries: list[dict[str, Any]] = []
    toc_pages: list[int] = []
    for page in (layout.get("pdf_info") or [])[:front_pages]:
        if not isinstance(page, dict):
            continue
        idx = int(page.get("page_idx") or 0) + 1
        titles = [
            str(b.get("type") or "") == "title" and _join_text(b.get("lines"))
            for b in page.get("para_blocks", []) or []
            if isinstance(b, dict)
        ]
        hit = any((t or "").replace(" ", "").lower() in _TOC_TITLES for t in titles)
        if not hit:
            continue
        toc_pages.append(idx)
        for block in page.get("para_blocks", []) or []:
            if not isinstance(block, dict) or str(block.get("type") or "") == "title":
                continue
            for line in block.get("lines", []) or []:
                if not isinstance(line, dict):
                    continue
                for span in line.get("spans", []) or []:
                    if not isinstance(span, dict):
                        continue
                    # 目录整页常被并成一个 span，内容里以 \n 分行
                    for piece in str(span.get("content") or "").split("\n"):
                        text = _join_text([{"spans": [{"type": "text", "content": piece}]}])
                        m = _LEADER.match(text)
                        if not m:
                            continue
                        title = m.group("title").strip()
                        entries.append({
                            "level": _level(title),
                            "title": title,
                            "print_page": _norm_num(m.group("page")),
                        })

    page_map = _toc_page_numbers(book, set(toc_pages))
    for entry in entries:
        entry["pdf_page"] = page_map.get(entry["print_page"])

    return {
        "book": book.name,
        "source": "ir_toc",
        "toc_pages": toc_pages,
        "entries": entries,
        "unresolved": sum(1 for e in entries if not e["pdf_page"]),
    }


def _diff_reference(book: Path, toc: dict[str, Any]) -> dict[str, Any]:
    """与人工 toc.json 对拍（只读参照）：按 print_page 配对算标题一致率。"""
    ref_path = book / "toc.json"
    if not ref_path.is_file():
        return {"reference": "missing"}
    try:
        ref = json.loads(ref_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"reference": "unreadable"}
    ref_entries = ref.get("entries") if isinstance(ref, dict) else None
    if not isinstance(ref_entries, list):
        return {"reference": "no-entries"}
    ref_by_page: dict[str, str] = {}
    for item in ref_entries:
        if not isinstance(item, dict):
            continue
        if item.get("from_body"):
            continue  # 正文回填的细目不在印刷目录里，不公平比对
        pp = str(item.get("print_page") or "").strip()
        title = str(item.get("label") or item.get("title") or "").strip()
        if pp and title:
            ref_by_page.setdefault(pp, title)
    matched = 0
    for entry in toc["entries"]:
        want = ref_by_page.get(entry["print_page"])
        if want is None:
            continue
        if re.sub(r"\s+", "", want) == re.sub(r"\s+", "", entry["title"]):
            matched += 1
    return {
        "reference_entries": len(ref_entries),
        "matched_titles": matched,
        "ir_entries": len(toc["entries"]),
    }


def ir_toc(book_id: str, root: str = "", apply: str = "scan") -> dict[str, Any]:
    sid = safe_book_id(book_id)
    mode = (apply or "scan").strip().lower()
    book = book_dir(sid, root)
    toc = build_toc(book)
    diff = _diff_reference(book, toc)
    if mode == "fix":
        out = book / "ir" / "toc.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(toc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                       encoding="utf-8")
        tmp.replace(out)
    return {
        "ok": True,
        "book_id": sid,
        "apply": mode,
        "toc_pages": toc["toc_pages"],
        "entries": len(toc["entries"]),
        "unresolved_pages": toc["unresolved"],
        **diff,
    }
