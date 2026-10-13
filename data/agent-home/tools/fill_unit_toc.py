from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import median
from typing import Any

try:
    from ir_lib import FRONT_PAGES
except ImportError:
    FRONT_PAGES = 24

_BODY_SHAPE = re.compile(
    r"^第[0-9０-９一二三四五六七八九十百]+[章单元课篇节讲]"
    r"|^[0-9０-９]+\.[0-9０-９]+"
    r"|^(内容提要|复习题|习题|练习|附录|前言|绪言|序言|后记|本章小结|单元学习任务|学习目标)"
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
ATX = re.compile(r"^#{1,6}\s+")
UNIT_LINE = re.compile(
    r"^\u7b2c\s*([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u96f6\u3007\u4e24\d]+)\s*\u5355\u5143(?:\s*(.+))?$"
)
BACK_LINE = re.compile(r"^\u540e\s*\u8bb0$")
TASK_LINE = re.compile(r"^\u5355\u5143\u5b66\u4e60\u4efb\u52a1$")
ACTIVITY_LINE = re.compile(
    r"^([\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+)\s+(.+)$"
)
LESSON_ONLY = re.compile(r"^\d{1,2}$")
LESSON_AND_WORK = re.compile(r"^(\*?)(\d{1,2})\s+(.+)$")
TRAIL_PAGE = re.compile(r"^(.*?)\s+(\d{1,3})$")
HTML_NAV = re.compile(
    r"<nav\b[^>]*class=\"[^\"]*kb-page-toc[\s\S]*?</nav>", re.I
)
HTML_OL = re.compile(
    r"<ol\b[^>]*class=\"[^\"]*kb-page-toc[\s\S]*?</ol>", re.I
)
HTML_FENCE = re.compile(r"::: kb-page-toc\r?\n([\s\S]*?)\r?\n:::")
HTML_LI = re.compile(r"<li\b[^>]*>(.*?)</li>", re.I | re.S)
HTML_TITLE = re.compile(r"kb-page-toc-title\">([^<]*)</span>")
HTML_PAGE = re.compile(r"kb-page-toc-page\">(\d+)</span>")
HTML_GOTO = re.compile(r'data-goto-(?:print-)?page="(\d+)"')
CN_DIGIT = {
    "\u96f6": 0,
    "\u3007": 0,
    "\u4e00": 1,
    "\u4e8c": 2,
    "\u4e24": 2,
    "\u4e09": 3,
    "\u56db": 4,
    "\u4e94": 5,
    "\u516d": 6,
    "\u4e03": 7,
    "\u516b": 8,
    "\u4e5d": 9,
}


def fill_unit_toc(book_id: str, root: str = "") -> dict[str, Any]:
    """Fill toc.json for books that use 第N单元 + lesson numbers. Pages are written by write_toc_pages."""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    book = _book_dir(sid, root)
    pages_dir = book / "pages"
    if not pages_dir.is_dir():
        raise FileNotFoundError(str(pages_dir))
    page_files = sorted(pages_dir.glob("*.md"))
    texts = {int(p.stem): p.read_text(encoding="utf-8") for p in page_files if p.stem.isdigit()}
    existing_early = _load_existing_unit_toc(book)
    if not any(_has_unit_shape(t) for t in texts.values()) and existing_early is None:
        return {
            "book_id": sid,
            "skipped": True,
            "reason": "no_第N单元",
            "entries": 0,
        }

    page_count = max(texts) if texts else 0
    toc_pages = _toc_page_nos(book, texts)
    existing = existing_early or _load_existing_unit_toc(book)
    parsed_pages: list[list[dict[str, Any]]] = []
    for n in toc_pages:
        parsed_pages.append(_parse_toc_page(texts.get(n, ""), page_count))
    parsed_entries = [e for chunk in parsed_pages for e in chunk]
    if existing:
        entries = existing
        reused = True
    else:
        entries = parsed_entries
        reused = False
    if not any(e["level"] == 0 and UNIT_LINE.match(e["label"]) for e in entries):
        return {
            "book_id": sid,
            "skipped": True,
            "reason": "toc_has_no_第N单元",
            "entries": 0,
        }

    if not reused:
        _attach_pdf_pages(entries, texts, toc_pages)
        _fill_lesson_pages_from_children(entries)
    prev_entries = existing if reused else _load_json_entries(book)
    _write_toc_json(book, entries, toc_pages)
    levels: dict[str, int] = {}
    for item in entries:
        key = str(item.get("level"))
        levels[key] = levels.get(key, 0) + 1
    return {
        "book_id": sid,
        "skipped": False,
        "ok": True,
        "entries": len(entries),
        "toc_pages": toc_pages,
        "toc_pages_dirty": list(toc_pages),
        "levels": levels,
        "toc_diff": _toc_diff_summary(prev_entries, entries),
        "reused_toc_json": reused,
        "wrote": "toc.json",
    }


def _book_dir(book_id: str, root: str) -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir():
        return base / "textbooks" / book_id
    return base / book_id


def _has_unit_shape(text: str) -> bool:
    for raw in text.splitlines():
        line = ATX.sub("", raw).strip()
        if UNIT_LINE.match(_strip_trail_page(line)[0]):
            return True
    return bool(
        re.search(
            r"第\s*[一二三四五六七八九十百零〇两\d]+\s*单元",
            text,
        )
    )


def _toc_page_nos(book: Path, texts: dict[int, str]) -> list[int]:
    listed: list[int] = []
    toc_path = book / "toc.json"
    if toc_path.is_file():
        try:
            data = json.loads(toc_path.read_text(encoding="utf-8"))
            listed = [int(x) for x in data.get("toc_pages") or [] if int(x) > 0]
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            listed = []
    found = [
        n
        for n, text in texts.items()
        if n <= FRONT_PAGES
        and (
            "kb-page-toc" in text
            or re.search(r"^#{0,6}\s*目\s*录\s*$", text, re.M)
            or re.search(r"^#{0,6}\s*Contents\s*$", text, re.I | re.M)
        )
    ]
    pages = sorted(set(listed) | set(found))
    return pages or [n for n in sorted(texts)[:6] if _has_unit_shape(texts[n])]


def _strip_trail_page(line: str, max_page: int = 0) -> tuple[str, int | None]:
    m = TRAIL_PAGE.match(line.strip())
    if not m:
        return line.strip(), None
    title, page_s = m.group(1).strip(), m.group(2)
    page = int(page_s)
    cap = int(max_page or 0)
    if page < 1 or (cap > 0 and page > cap):
        return line.strip(), None
    if title.endswith("/") or not title:
        return line.strip(), None
    return title, page


def _norm_title(title: str) -> str:
    out = re.sub(r"\s+", " ", title).strip()
    out = re.sub(r"\s*/\s*", " / ", out)
    return out


def _parse_toc_page(text: str, max_page: int = 0) -> list[dict[str, Any]]:
    rows: list[tuple[str, int | None]] = []
    blocks = HTML_NAV.findall(text) or HTML_OL.findall(text)
    for block in blocks:
        for li in HTML_LI.findall(block):
            tm = HTML_TITLE.search(li)
            if not tm:
                continue
            title = _norm_title(tm.group(1))
            pm = HTML_PAGE.search(li) or HTML_GOTO.search(li)
            page = int(pm.group(1)) if pm else None
            if page is not None and max_page > 0 and page > max_page:
                page = None
            if title:
                rows.append((title, page))
    if not rows:
        for inner in HTML_FENCE.findall(text):
            text = inner
            break
        for raw in text.splitlines():
            line = ATX.sub("", raw).strip()
            line = re.sub(r"^[-*·]\s*", "", line)
            if not line or line in {"目录", "目 录", "Contents"} or line.startswith(":::"):
                continue
            title, page = _strip_trail_page(line, max_page)
            title = _norm_title(title)
            if title:
                rows.append((title, page))
    return _rows_to_entries(rows)


def _load_existing_unit_toc(book: Path) -> list[dict[str, Any]] | None:
    path = book / "toc.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        return None
    cleaned: list[dict[str, Any]] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        label = _norm_title(str(raw.get("label") or ""))
        if not label:
            continue
        cleaned.append(
            {
                "label": label,
                "print_page": raw.get("print_page"),
                "is_chapter": bool(raw.get("is_chapter")),
                "level": int(raw.get("level") or 0),
                "pdf_page": int(raw.get("pdf_page") or 0),
            }
        )
    if not any(e["level"] == 0 and UNIT_LINE.match(e["label"]) for e in cleaned):
        return None
    return cleaned


def _rows_to_entries(rows: list[tuple[str, int | None]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    current_lesson = False
    pending_unit: dict[str, Any] | None = None

    def flush_unit() -> None:
        nonlocal pending_unit
        if pending_unit is not None:
            entries.append(pending_unit)
            pending_unit = None

    def add(label: str, level: int, chapter: bool, page: int | None) -> None:
        entries.append(
            {
                "label": label,
                "print_page": page,
                "is_chapter": chapter,
                "level": level,
                "pdf_page": 0,
            }
        )

    for title, page in rows:
        um = UNIT_LINE.match(title)
        if um:
            flush_unit()
            rest = (um.group(2) or "").strip()
            label = f"第{_norm_cn_num(um.group(1))}单元"
            if rest:
                label = f"{label} {rest}"
            pending_unit = {
                "label": label,
                "print_page": page,
                "is_chapter": True,
                "level": 0,
                "pdf_page": 0,
            }
            current_lesson = False
            continue
        if BACK_LINE.match(title):
            flush_unit()
            add("后记", 0, True, page)
            current_lesson = False
            continue
        if TASK_LINE.match(title):
            flush_unit()
            add("单元学习任务", 1, False, page)
            current_lesson = False
            continue
        am = ACTIVITY_LINE.match(title)
        if am and not UNIT_LINE.match(title):
            flush_unit()
            add(_norm_title(title), 1, False, page)
            current_lesson = False
            continue
        if LESSON_ONLY.match(title):
            flush_unit()
            add(title, 1, False, page)
            current_lesson = True
            continue
        lm = LESSON_AND_WORK.match(title)
        if lm:
            flush_unit()
            star, num, work = lm.group(1), lm.group(2), lm.group(3).strip()
            add(num, 1, False, page)
            work = _norm_title(f"{star}{work}")
            add(work, 2, False, page)
            current_lesson = True
            continue
        if pending_unit is not None and not current_lesson and "/" not in title and not title.startswith("《"):
            extra = title
            if extra not in pending_unit["label"]:
                pending_unit["label"] = f"{pending_unit['label']} {extra}".strip()
            if page and pending_unit["print_page"] is None:
                pending_unit["print_page"] = page
            flush_unit()
            current_lesson = False
            continue
        flush_unit()
        bare = 2 <= len(title) <= 12 and "/" not in title and not title.startswith("《")
        if current_lesson:
            add(title, 2, False, page)
        elif bare:
            add(title, 0, True, page)
            current_lesson = False
        else:
            add(title, 1, False, page)
    flush_unit()
    return entries


def _norm_cn_num(token: str) -> str:
    token = token.strip()
    if token.isdigit():
        return str(int(token))
    if token in CN_DIGIT:
        return token
    if token.startswith("十") and len(token) <= 2:
        return token
    return token


def _plain_pages(text: str) -> str:
    out = HTML_NAV.sub(" ", text)
    out = re.sub(r"<[^>]+>", " ", out)
    return out


def _first_body_page(key: str, texts: dict[int, str], skip: set[int]) -> int | None:
    key = key.lstrip("*").strip()
    if "/" in key:
        key = key.split("/", 1)[0].strip()
    key = re.sub(r"[（(][^）)]*[）)]", "", key).strip()
    if len(key) < 2:
        return None
    if len(key) < 4 and not _BODY_SHAPE.match(key):
        return None
    for n in sorted(texts):
        if n in skip:
            continue
        blob = _plain_pages(texts[n])
        if key in blob:
            return n
    return None


def _attach_pdf_pages(
    entries: list[dict[str, Any]], texts: dict[int, str], toc_pages: list[int]
) -> None:
    skip = set(toc_pages)
    offsets: list[int] = []
    for e in entries:
        if e["level"] == 1 and LESSON_ONLY.match(e["label"]):
            continue
        pdf = _first_body_page(e["label"], texts, skip)
        if pdf is None:
            continue
        e["pdf_page"] = pdf
        if e["print_page"]:
            offsets.append(pdf - int(e["print_page"]))
    offset = int(median(offsets)) if offsets else 0
    for e in entries:
        if e["pdf_page"]:
            continue
        if e["print_page"] and offset:
            e["pdf_page"] = int(e["print_page"]) + offset
        elif e["print_page"]:
            e["pdf_page"] = int(e["print_page"])
    for e in entries:
        if e["print_page"] is None and e["pdf_page"] and offset:
            guess = e["pdf_page"] - offset
            if guess > 0:
                e["print_page"] = guess


def _fill_lesson_pages_from_children(entries: list[dict[str, Any]]) -> None:
    i = 0
    while i < len(entries):
        e = entries[i]
        if e["level"] == 1 and LESSON_ONLY.match(e["label"]):
            j = i + 1
            child_pdf = 0
            child_print = None
            while j < len(entries) and entries[j]["level"] == 2:
                if not child_pdf and entries[j]["pdf_page"]:
                    child_pdf = int(entries[j]["pdf_page"])
                    child_print = entries[j]["print_page"]
                j += 1
            if child_pdf:
                e["pdf_page"] = child_pdf
                if e["print_page"] is None:
                    e["print_page"] = child_print
        i += 1


def _load_json_entries(book: Path) -> list[dict[str, Any]]:
    path = book / "toc.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("entries") if isinstance(data, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _toc_diff_summary(prev: list[dict[str, Any]], nxt: list[dict[str, Any]]) -> dict[str, int]:
    old_map = {(str(item.get("label") or ""), item.get("print_page")): item.get("level") for item in prev}
    new_map = {(str(item.get("label") or ""), item.get("print_page")): item.get("level") for item in nxt}
    return {
        "added": sum(1 for key in new_map if key not in old_map),
        "removed": sum(1 for key in old_map if key not in new_map),
        "level_changed": sum(1 for key in new_map if key in old_map and old_map[key] != new_map[key]),
    }


def _write_toc_json(book: Path, entries: list[dict[str, Any]], toc_pages: list[int]) -> None:
    mapping: dict[str, int] = {}
    for e in entries:
        if e["print_page"] and e["pdf_page"]:
            mapping[str(int(e["print_page"]))] = int(e["pdf_page"])
    payload = {
        "entries": entries,
        "print_to_pdf": mapping,
        "toc_pages": toc_pages,
        "source": "printed",
    }
    path = book / "toc.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
