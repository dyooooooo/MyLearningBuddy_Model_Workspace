"""New-line TOC processor: derive a book outline from the canonical IR.

Prefers ``ir/pages/*.json`` (schema-2 archive). Falls back to MinerU
``layout.json`` only when those pages are missing (tests / pre-ingest).

Parses dotted leaders and slash leaders (``标题 / 2``), glues split
chapter titles (``第 章 1`` + ``发酵工程1``), and maps print pages to
pdf pages via chrome / discarded page-number blocks. Writes
``textbooks/<id>/ir/toc.json`` on apply=fix. Human ``toc.json`` is a
read-only diff reference and is never written.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ir_lib import _join_text, book_dir, load_layout, load_page_json, safe_book_id

_TOC_TITLES = {"目录", "目 录", "目  录", "contents", "contens", "目 録"}
_LEADER = re.compile(r"^(?P<title>.+?)[\s　]*[.．·•‥…⋯]{2,}[\s　]*(?P<page>\d{1,3}|[IVXLC]+)$", re.I)
_SLASH = re.compile(r"^(?P<title>.+?)[\s　]*/[\s　]*(?P<page>\d{1,3}|[IVXLC]+)$", re.I)
_SPLIT_CHAP = re.compile(r"^第\s*章\s*(?P<num>[0-9０-９一二三四五六七八九十百]+)$")
_TITLE_TAIL_PAGE = re.compile(r"^(?P<title>.+?)(?P<page>\d{1,3})$")
_LEVEL_PATTERNS = [
    (re.compile(r"^第[0-9０-９一二三四五六七八九十百]+[章单元课篇]"), 0),
    (re.compile(r"^(附录|后记|序言|前言|绪言|introduction|appendix)", re.I), 0),
    (re.compile(r"^[0-9０-９]+\.[0-9０-９]+\.[0-9０-９]+"), 2),
    (re.compile(r"^[0-9０-９]+\.[0-9０-９]+"), 1),
    (re.compile(r"^unit\s+[0-9]+", re.I), 0),
    (re.compile(r"^[0-9０-９]+\s"), 1),
]
_FRONT_PAGES = 24


def _level(title: str) -> int:
    head = title.strip()
    for pattern, level in _LEVEL_PATTERNS:
        if pattern.match(head):
            return level
    return 1


def _norm_num(raw: str) -> str:
    return raw.strip().lstrip("0") or "0"


def _is_toc_title(text: str) -> bool:
    return (text or "").replace(" ", "").lower() in {t.replace(" ", "").lower() for t in _TOC_TITLES}


def _parse_leader(text: str) -> tuple[str, str] | None:
    piece = (text or "").strip()
    if not piece:
        return None
    matched = _LEADER.match(piece) or _SLASH.match(piece)
    if not matched:
        return None
    title = matched.group("title").strip()
    if not title or _is_toc_title(title):
        return None
    return title, _norm_num(matched.group("page"))


def _decorate(entry: dict[str, Any]) -> dict[str, Any]:
    title = str(entry.get("title") or "").strip()
    level = int(entry.get("level") if entry.get("level") is not None else _level(title))
    print_page = entry.get("print_page")
    if print_page is not None:
        print_page = str(print_page).strip()
    out = {
        "level": level,
        "title": title,
        "label": title,
        "print_page": print_page or None,
        "pdf_page": entry.get("pdf_page"),
        "is_chapter": level == 0,
    }
    return out


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


def _ir_page_nos(book: Path) -> list[int]:
    folder = book / "ir" / "pages"
    if not folder.is_dir():
        return []
    out: list[int] = []
    for path in folder.glob("*.json"):
        stem = path.name[:4]
        if stem.isdigit():
            out.append(int(stem))
    return sorted(out)


def _chrome_print(page: dict[str, Any]) -> str | None:
    nums = (page.get("chrome") or {}).get("page_numbers") or []
    for raw in nums:
        m = re.search(r"\d{1,3}", str(raw or ""))
        if m:
            return _norm_num(m.group(0))
    return None


def _page_map_from_ir(book: Path, skip_pages: set[int]) -> dict[str, int]:
    out = _toc_page_numbers(book, skip_pages)
    for idx in _ir_page_nos(book):
        if idx in skip_pages:
            continue
        page = load_page_json(book, idx)
        if not page:
            continue
        printed = _chrome_print(page)
        if printed:
            out.setdefault(printed, idx)
    return out


def _apply_page_map(entries: list[dict[str, Any]], page_map: dict[str, int]) -> list[dict[str, Any]]:
    decorated: list[dict[str, Any]] = []
    for raw in entries:
        row = _decorate(raw)
        printed = row.get("print_page")
        if row.get("pdf_page") is None and printed:
            row["pdf_page"] = page_map.get(str(printed))
        decorated.append(row)
    return decorated


def _layout_titles(page: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for block in page.get("para_blocks", []) or []:
        if not isinstance(block, dict) or str(block.get("type") or "") != "title":
            continue
        titles.append(_join_text(block.get("lines")))
    return titles


def _layout_pieces(page: dict[str, Any]) -> list[str]:
    pieces: list[str] = []
    for block in page.get("para_blocks", []) or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "") == "title":
            pieces.append(_join_text(block.get("lines")))
            continue
        for line in block.get("lines", []) or []:
            if not isinstance(line, dict):
                continue
            for span in line.get("spans", []) or []:
                if not isinstance(span, dict):
                    continue
                for piece in str(span.get("content") or "").split("\n"):
                    pieces.append(_join_text([{"spans": [{"type": "text", "content": piece}]}]))
    return pieces


def _ir_titles(page: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for block in page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "") != "title":
            continue
        if str(block.get("kind") or "") == "box":
            continue
        titles.append(str(block.get("text") or "").strip())
    return titles


def _ir_pieces(page: dict[str, Any]) -> list[tuple[str, str]]:
    """(kind, text) in reading order. kind is title or text."""
    out: list[tuple[str, str]] = []
    for block in page.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        kind = "title" if str(block.get("type") or "") == "title" else "text"
        if str(block.get("kind") or "") == "box":
            continue
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if kind == "title":
            out.append(("title", text))
            continue
        for piece in text.split("\n"):
            piece = piece.strip()
            if piece:
                out.append(("text", piece))
    return out


def _looks_toc(titles: list[str], pieces: list[str]) -> bool:
    if any(_is_toc_title(t) for t in titles):
        return True
    if sum(1 for p in pieces if _parse_leader(p)) >= 2:
        return True
    if any(_SPLIT_CHAP.match((t or "").strip()) for t in titles):
        return True
    return False


def _collect_from_pieces(items: list[tuple[str, str]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    pending: str | None = None
    for kind, text in items:
        split = _SPLIT_CHAP.match(text.strip())
        if kind == "title" and split:
            pending = _norm_num(split.group("num"))
            continue
        if kind == "title" and pending:
            tail = _TITLE_TAIL_PAGE.match(text.strip())
            if tail and not _parse_leader(text):
                name = tail.group("title").strip()
                printed = _norm_num(tail.group("page"))
                if name:
                    title = f"第{pending}章 {name}"
                    entries.append({
                        "level": 0,
                        "title": title,
                        "print_page": printed,
                    })
                    pending = None
                    continue
            pending = None
        parsed = _parse_leader(text)
        if parsed:
            title, printed = parsed
            entries.append({
                "level": _level(title),
                "title": title,
                "print_page": printed,
            })
    return entries


def _heading_fallback(book: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for idx in _ir_page_nos(book):
        page = load_page_json(book, idx)
        if not page:
            continue
        printed = _chrome_print(page)
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "") != "title":
                continue
            if str(block.get("kind") or "") == "box":
                continue
            text = str(block.get("text") or "").strip()
            if not text or _is_toc_title(text) or _SPLIT_CHAP.match(text):
                continue
            if not any(pattern.match(text) for pattern, _ in _LEVEL_PATTERNS):
                continue
            key = (re.sub(r"\s+", "", text), idx)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                "level": _level(text),
                "title": text,
                "print_page": printed,
                "pdf_page": idx,
            })
    return entries


def _from_ir_pages(book: Path) -> tuple[list[int], list[dict[str, Any]]]:
    toc_pages: list[int] = []
    entries: list[dict[str, Any]] = []
    pages = _ir_page_nos(book)
    if not pages:
        return toc_pages, entries
    started = False
    miss = 0
    for idx in pages:
        if not started and idx > _FRONT_PAGES:
            break
        page = load_page_json(book, idx)
        if not page:
            continue
        titles = _ir_titles(page)
        items = _ir_pieces(page)
        pieces = [text for _, text in items]
        hit = _looks_toc(titles, pieces)
        if not started:
            if not hit:
                continue
            started = True
        elif not hit:
            miss += 1
            if miss >= 2:
                break
            continue
        else:
            miss = 0
        toc_pages.append(idx)
        entries.extend(_collect_from_pieces(items))
    return toc_pages, entries


def _from_layout(book: Path) -> tuple[list[int], list[dict[str, Any]]]:
    layout = load_layout(book)
    if not layout:
        raise FileNotFoundError(f"missing layout.json under {book / 'mineru'}")
    toc_pages: list[int] = []
    entries: list[dict[str, Any]] = []
    started = False
    miss = 0
    for page in layout.get("pdf_info") or []:
        if not isinstance(page, dict):
            continue
        idx = int(page.get("page_idx") or 0) + 1
        if not started and idx > _FRONT_PAGES:
            break
        titles = _layout_titles(page)
        pieces = _layout_pieces(page)
        items = [("title", t) for t in titles] + [("text", p) for p in pieces]
        hit = _looks_toc(titles, pieces)
        if not started:
            if not hit:
                continue
            started = True
        elif not hit:
            miss += 1
            if miss >= 2:
                break
            continue
        else:
            miss = 0
        toc_pages.append(idx)
        # layout pieces already include titles; collect once from pieces as text
        # so dotted-leader body still matches. Split-chapter titles need title kind.
        ordered: list[tuple[str, str]] = []
        for block in page.get("para_blocks", []) or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "") == "title":
                ordered.append(("title", _join_text(block.get("lines"))))
                continue
            for line in block.get("lines", []) or []:
                if not isinstance(line, dict):
                    continue
                for span in line.get("spans", []) or []:
                    if not isinstance(span, dict):
                        continue
                    for piece in str(span.get("content") or "").split("\n"):
                        text = _join_text([{"spans": [{"type": "text", "content": piece}]}])
                        if text:
                            ordered.append(("text", text))
        entries.extend(_collect_from_pieces(ordered))
    return toc_pages, entries


def build_toc(book: Path, front_pages: int = _FRONT_PAGES) -> dict[str, Any]:
    del front_pages  # kept for callers; scan window is _FRONT_PAGES + continuation
    if not load_layout(book) and not _ir_page_nos(book):
        raise FileNotFoundError(f"missing layout.json under {book / 'mineru'}")
    if _ir_page_nos(book):
        toc_pages, entries = _from_ir_pages(book)
    else:
        toc_pages, entries = _from_layout(book)
    if not entries:
        entries = _heading_fallback(book)
    page_map = _page_map_from_ir(book, set(toc_pages))
    decorated = _apply_page_map(entries, page_map)
    nxt: int | None = None
    for row in reversed(decorated):
        pdf = row.get("pdf_page")
        if pdf:
            nxt = int(pdf)
        elif nxt is not None:
            row["pdf_page"] = nxt
    return {
        "book": book.name,
        "source": "ir_toc",
        "toc_pages": toc_pages,
        "entries": decorated,
        "unresolved": sum(1 for e in decorated if not e.get("pdf_page")),
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
        printed = str(entry.get("print_page") or "").strip()
        want = ref_by_page.get(printed)
        if want is None:
            continue
        got = str(entry.get("title") or "")
        if re.sub(r"\s+", "", want) == re.sub(r"\s+", "", got):
            matched += 1
    return {
        "reference_entries": len(ref_entries),
        "matched_titles": matched,
        "ir_entries": len(toc["entries"]),
    }


def _textbooks_root(root: str = "") -> Path:
    base = Path(root).resolve() if root else Path.cwd()
    return base / "textbooks" if (base / "textbooks").is_dir() else base


def _book_ids(root: str = "") -> list[str]:
    folder = _textbooks_root(root)
    if not folder.is_dir():
        return []
    out: list[str] = []
    for path in sorted(folder.iterdir()):
        if not path.is_dir():
            continue
        if (path / "ir" / "pages").is_dir() or (path / "mineru" / "layout.json").is_file():
            out.append(path.name)
    return out


def ir_toc(book_id: str, root: str = "", apply: str = "scan") -> dict[str, Any]:
    raw = (book_id or "").strip()
    if raw in ("", "*"):
        rows = [ir_toc(bid, root, apply) for bid in _book_ids(root)]
        failed = [r.get("book_id") for r in rows if not r.get("ok")]
        return {
            "ok": not failed,
            "book_id": "*",
            "apply": (apply or "scan").strip().lower(),
            "books": len(rows),
            "entries": sum(int(r.get("entries") or 0) for r in rows),
            "empty": sum(1 for r in rows if int(r.get("entries") or 0) == 0),
            "failed": failed,
        }
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
