"""New-line TOC processor: derive a book outline from the canonical IR.

Prefers ``ir/pages/*.json`` (schema-2 archive). Falls back to MinerU
``layout.json`` only when those pages are missing (tests / pre-ingest).

When ``toc.print`` found the printed catalog, its rows are the skeleton
(the old line's method): body title blocks only fix each row's PDF page
and hang below the row whose pages they sit on (``from_body``).
Otherwise it collects every title block on intermediate pages, down to
the smallest level. Pages that are the printed table of contents are
skipped. The same title text is kept once, the first time it appears.
``apply=fix`` writes ``textbooks/<id>/ir/toc.json`` and will not
overwrite a file already marked ``reviewed`` unless ``force=True`` or
the file predates the current ``toc.print`` skeleton. ``apply=review``
sends each item to the knowledge-base agent with that page's title
blocks, then writes the polished list back. Human ``toc.json`` is a
read-only diff reference and is never written.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import ir_print_toc
from ir_lib import FRONT_PAGES, _join_text, book_dir, load_layout, load_page_json, safe_book_id

_TOC_TITLES = {"目录", "目 录", "目  录", "contents", "contens", "目 録"}
_LEADER = re.compile(r"^(?P<title>.+?)[\s　]*[.．·•‥…⋯]{2,}[\s　]*(?P<page>\d{1,3}|[IVXLC]+)$", re.I)
_SLASH = re.compile(r"^(?P<title>.+?)[\s　]*/[\s　]*(?P<page>\d{1,3}|[IVXLC]+)$", re.I)
_SPLIT_CHAP = re.compile(r"^第\s*章\s*(?P<num>[0-9０-９一二三四五六七八九十百]+)$")
_TITLE_TAIL_PAGE = re.compile(r"^(?P<title>.+?)(?P<page>\d{1,3})$")
_LEVEL_PATTERNS = [
    (re.compile(r"^第[0-9０-９一二三四五六七八九十百]+[章单元课篇]"), 0),
    # 「第一节 质点 物理模型」是节级；节/讲不能混进上一条，否则章号判定会乱。
    (re.compile(r"^第[0-9０-９一二三四五六七八九十百]+[节讲]"), 1),
    (re.compile(r"^(附录|后记|序言|前言|绪言|introduction|appendix)", re.I), 0),
    (re.compile(r"^[0-9０-９]+\.[0-9０-９]+\.[0-9０-９]+"), 2),
    (re.compile(r"^[0-9０-９]+\.[0-9０-９]+"), 1),
    (re.compile(r"^unit\s+[0-9]+", re.I), 0),
    (re.compile(r"^[0-9０-９]+\s"), 1),
]
_FRONT_PAGES = FRONT_PAGES
# 版权页（开本 / 印张 / 主编 / 责任编辑 …）会被斜杠规则误当成目录行
# （「开 本 890×1240 1/16」），而且它总排在真目录之前。这些行既不参与
# 目录页判定，也不收成条目。
_PUBLISHER = re.compile(
    r"开\s*本|印\s*张|字\s*数|版\s*次|印\s*次|书\s*号|定\s*价|ISBN|"
    r"主\s*编|编写人员|责任编辑|装帧设计|图片提供|出版发行|出版社|"
    r"印\s*刷|经\s*销|邮政编码|毫\s*米|[0-9０-９]{3}\s*(?:[×xX*]|\\times)\s*[0-9０-９]{3}",
    re.I,
)
# 真目录常写「1.1 集合初步 2」——只有空格，没有点线也没有斜杠。
_SPACE_PAGE = re.compile(r"^(?P<title>.+?)[\s　]+(?P<page>\d{1,3})$")
# 空格式只在标题形状像章节/固定栏目时才收，避免把普通句子的尾数字当页码。
_ENTRY_EXTRA_SHAPES = re.compile(
    # 固定栏目名：后面必须断开（行尾、空白、编号、标点），否则「实验室里有 3」
    # 这种普通句子会被「实验」前缀收进目录。
    r"^(内容提要|复习题|习题|练习|阅读|探究|实验|活动|附录|索引|前言|绪言|序言|后记|"
    r"本章小结|学习目标)(?=$|[\s　0-9０-９（(：:、·．.－—-])"
    # 可以自由接续的栏目：单元研习任务、专题一、综合实践活动 …
    r"|^(单元|专题|课题|项目|综合实践|章末)",
)
_STAR_HEAD = "*＊·•◆"
# 分栏收口块（kb-close-* / kb-algo-*）是渲染派生物，与原块同文；
# 一起数会让每条目录出现两遍。
_DERIVED_PREFIX = ("kb-close-", "kb-algo-")
# 条目太少（或全是版权噪声）就改用正文标题回退。
_MIN_ENTRIES = 3
# 印刷页码候选：整块数字（「7」）、书眉开头数字（「2第一章 运动的描述」）、
# 书眉结尾数字。跟着小数点的数字不算（「1.1 集合初步」是书眉不是页码）。
_NUM_ONLY = re.compile(r"^(\d{1,3})$")
_NUM_HEAD = re.compile(r"^(\d{1,3})(?=[^\d.．])")
_NUM_TAIL = re.compile(r"(?<![\d.．])(\d{1,3})$")
# MinerU 常把页脚数字归成 header/footer，不止 page_number。
_CHROME_TYPES = {"page_number", "page-number", "header", "footer"}
# 偏移量至少要这么多页支持才采信，免得被个别误读带偏。
_OFFSET_MIN_SUPPORT = 5
# 正文标题回退时，只有弱形状（「1 xxx」）才卡长度：英语书的练习题号
# 「1 Look at the photos on pages 6 and 7 …」会被当成章节标题。
_WEAK_SHAPE = re.compile(r"^[0-9０-９]+\s")
_WEAK_MAX_CHARS = 24


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


def _is_publisher_noise(text: str) -> bool:
    return bool(_PUBLISHER.search(text or ""))


def _looks_entry_title(title: str) -> bool:
    """空格式条目的标题形状：编号章节，或固定栏目名（内容提要 / 复习题 …）。"""
    head = (title or "").strip().lstrip(_STAR_HEAD).strip()
    if not head:
        return False
    if any(pattern.match(head) for pattern, _ in _LEVEL_PATTERNS):
        return True
    return bool(_ENTRY_EXTRA_SHAPES.match(head))


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


def _parse_entry(text: str) -> tuple[str, str] | None:
    """一条目录行 → (标题, 印刷页)。点线/斜杠优先，再试空格式。"""
    piece = (text or "").strip()
    if not piece or _is_publisher_noise(piece):
        return None
    hit = _parse_leader(piece)
    if hit:
        return hit
    matched = _SPACE_PAGE.match(piece)
    if not matched:
        return None
    title = matched.group("title").strip()
    if not title or _is_toc_title(title) or not _looks_entry_title(title):
        return None
    return title, _norm_num(matched.group("page"))


def _bare_chapter(text: str) -> str | None:
    """目录页上不带页码的章名（「第1章 集合与逻辑」），章首页常不印页码。"""
    piece = (text or "").strip()
    if not piece or _is_publisher_noise(piece) or _SPLIT_CHAP.match(piece):
        return None
    if _parse_entry(piece) or _TITLE_TAIL_PAGE.match(piece):
        return None
    if _LEVEL_PATTERNS[0][0].match(piece) or re.match(r"^unit\s+[0-9]+", piece, re.I):
        return piece
    return None


def _is_derived(block: dict[str, Any]) -> bool:
    return str(block.get("id") or "").startswith(_DERIVED_PREFIX)


def _entry_level(raw: Any, *, title: str = "") -> int:
    """Parse outline level. ``0`` is a valid chapter level — never use ``raw or 1``."""
    if raw is not None and raw != "":
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    head = title.strip()
    return _level(head) if head else 1


def _norm_toc_title(text: str) -> str:
    return re.sub(r"[\s*＊]", "", text or "")


def _human_print_page_ref(book: Path) -> dict[tuple[str, int | None], str | None]:
    """Read-only map from human ``toc.json`` for filling ``print_page`` on imports."""
    path = book / "toc.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    ref: dict[tuple[str, int | None], str | None] = {}
    for item in data.get("entries") or []:
        if not isinstance(item, dict) or item.get("from_body"):
            continue
        label = str(item.get("label") or item.get("title") or "").strip()
        if not label:
            continue
        pp = item.get("print_page")
        pp_s = str(pp).strip() if pp is not None and str(pp).strip() else None
        try:
            pdf = int(item.get("pdf_page") or 0) or None
        except (TypeError, ValueError):
            pdf = None
        ref[(_norm_toc_title(label), pdf)] = pp_s
        ref.setdefault((_norm_toc_title(label), None), pp_s)
    return ref


def _decorate(entry: dict[str, Any]) -> dict[str, Any]:
    title = str(entry.get("title") or "").strip()
    level = _entry_level(entry.get("level"), title=title)
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
        text = raw.get("text") if isinstance(raw, dict) else raw
        m = re.search(r"\d{1,3}", str(text or ""))
        if m:
            return _norm_num(m.group(0))
    return None


def _number_candidates(text: str) -> list[str]:
    """页眉/页脚里的印刷页码。

    不少书的页码被 MinerU 归成 header 且与书眉粘在一起（「2第一章 运动的描述」、
    「第二节 位置的变化 位移」+「7」），所以整串、开头数字、结尾数字都当候选。
    「1.1 集合初步」这种书眉不会被当成页码（数字后面跟着点）。
    """
    piece = str(text or "").strip()
    if not piece:
        return []
    out: list[str] = []
    whole = _NUM_ONLY.match(piece)
    if whole:
        return [_norm_num(whole.group(1))]
    head = _NUM_HEAD.match(piece)
    if head:
        out.append(_norm_num(head.group(1)))
    tail = _NUM_TAIL.search(piece)
    if tail:
        out.append(_norm_num(tail.group(1)))
    return out


def _chrome_candidates(page: dict[str, Any]) -> list[str]:
    chrome = page.get("chrome") if isinstance(page.get("chrome"), dict) else {}
    out: list[str] = []
    for key in ("page_numbers", "headers", "footers"):
        for raw in chrome.get(key) or []:
            text = raw.get("text") if isinstance(raw, dict) else raw
            out.extend(_number_candidates(str(text or "")))
    return out


def _layout_candidates(book: Path) -> dict[int, list[str]]:
    layout = load_layout(book) or {}
    out: dict[int, list[str]] = {}
    for page in layout.get("pdf_info", []) or []:
        if not isinstance(page, dict):
            continue
        idx = int(page.get("page_idx") or 0) + 1
        found: list[str] = []
        for block in page.get("discarded_blocks", []) or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "") not in _CHROME_TYPES:
                continue
            found.extend(_number_candidates(_join_text(block.get("lines"))))
        if found:
            out[idx] = found
    return out


def _body_offset(candidates: dict[int, list[str]]) -> int | None:
    """正文页码与 pdf 页差一个常数。按整本投票取这个偏移量。

    逐页直接采信容易被前言/目录的另一套页码或书眉里的章号带偏；投票只认
    全书最一致的那个差值，个别误读的候选自动被压下去。
    """
    votes: dict[int, int] = {}
    for pdf_page, nums in candidates.items():
        for raw in set(nums):
            try:
                printed = int(raw)
            except ValueError:
                continue
            if printed <= 0:
                continue
            offset = pdf_page - printed
            if offset < 0:
                continue
            votes[offset] = votes.get(offset, 0) + 1
    if not votes:
        return None
    best, support = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))
    return best if support >= _OFFSET_MIN_SUPPORT else None


def _page_map_from_ir(book: Path, skip_pages: set[int], after: int = 0) -> dict[str, int]:
    ir_pages = _ir_page_nos(book)
    layout_cands = _layout_candidates(book)
    candidates: dict[int, list[str]] = {}
    for idx in sorted(set(ir_pages) | set(layout_cands)):
        if idx in skip_pages or idx <= after:
            continue
        found = list(layout_cands.get(idx) or [])
        page = load_page_json(book, idx) if idx in ir_pages else None
        if page:
            found.extend(_chrome_candidates(page))
        if found:
            candidates[idx] = found

    out: dict[str, int] = {}
    offset = _body_offset(candidates)
    if offset is not None:
        last = max(candidates) if candidates else 0
        for printed in range(1, max(0, last - offset) + 1):
            out[str(printed)] = printed + offset

    # 投票不成立（或个别页用了别的编号）时，回到逐页直接证据。
    for idx in sorted(candidates):
        for raw in candidates[idx]:
            out.setdefault(raw, idx)
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
        if str(block.get("kind") or "") == "box" or _is_derived(block):
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
        if str(block.get("kind") or "") == "box" or _is_derived(block):
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
    if sum(1 for p in pieces if _parse_entry(p)) >= 2:
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
        parsed = _parse_entry(text)
        if parsed:
            title, printed = parsed
            entries.append({
                "level": _level(title),
                "title": title,
                "print_page": printed,
            })
            continue
        chapter = _bare_chapter(text)
        if chapter:
            entries.append({
                "level": 0,
                "title": chapter,
                "print_page": None,
            })
    return entries


def _dedupe(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一条目录只留一条：分栏收口块与跨页重复都会让条目出现两遍。"""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in entries:
        key = (
            re.sub(r"\s+", "", str(row.get("title") or "")),
            str(row.get("print_page") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


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
            if str(block.get("kind") or "") == "box" or _is_derived(block):
                continue
            text = str(block.get("text") or "").strip()
            if not text or _is_toc_title(text) or _SPLIT_CHAP.match(text):
                continue
            if _is_publisher_noise(text):
                continue
            if not any(pattern.match(text) for pattern, _ in _LEVEL_PATTERNS):
                continue
            strong = any(
                pattern.match(text)
                for pattern, _ in _LEVEL_PATTERNS
                if pattern.pattern != _WEAK_SHAPE.pattern
            )
            if not strong and len(text) > _WEAK_MAX_CHARS:
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


_Record = tuple[list[str], list[str], list[tuple[str, str]]]


def _scan_pages(
    page_nos: list[int],
    record_of: Callable[[int], _Record | None],
) -> tuple[list[int], list[dict[str, Any]]]:
    """挑出目录页并收条目。

    标题写着「目录 / Contents」的页**优先**：版权页的「开 本 890×1240 1/16」
    过去会先启动扫描，随后连续两页不像目录就停，真目录永远到不了。找到显式
    目录页后就从它开始，之前的假命中一律跳过。
    """
    cache: dict[int, _Record | None] = {}

    def rec(page_no: int) -> _Record | None:
        if page_no not in cache:
            cache[page_no] = record_of(page_no)
        return cache[page_no]

    start_at: int | None = None
    for page_no in page_nos:
        if page_no > _FRONT_PAGES:
            break
        row = rec(page_no)
        if row and any(_is_toc_title(t) for t in row[0]):
            start_at = page_no
            break

    toc_pages: list[int] = []
    entries: list[dict[str, Any]] = []
    started = False
    miss = 0
    for page_no in page_nos:
        if start_at is not None and page_no < start_at:
            continue
        if not started and page_no > _FRONT_PAGES:
            break
        row = rec(page_no)
        if row is None:
            continue
        titles, pieces, items = row
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
        toc_pages.append(page_no)
        entries.extend(_collect_from_pieces(items))
    return toc_pages, entries


def _from_ir_pages(book: Path) -> tuple[list[int], list[dict[str, Any]]]:
    pages = _ir_page_nos(book)
    if not pages:
        return [], []

    def record_of(page_no: int) -> _Record | None:
        page = load_page_json(book, page_no)
        if not page:
            return None
        items = _ir_pieces(page)
        return _ir_titles(page), [text for _, text in items], items

    return _scan_pages(pages, record_of)


def _layout_ordered(page: dict[str, Any]) -> list[tuple[str, str]]:
    """layout 的 (kind, text)：点线正文按 text 收，拆开的章名需要 title 类型。"""
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
    return ordered


def _from_layout(book: Path) -> tuple[list[int], list[dict[str, Any]]]:
    layout = load_layout(book)
    if not layout:
        raise FileNotFoundError(f"missing layout.json under {book / 'mineru'}")
    by_page: dict[int, dict[str, Any]] = {}
    for page in layout.get("pdf_info") or []:
        if not isinstance(page, dict):
            continue
        by_page[int(page.get("page_idx") or 0) + 1] = page

    def record_of(page_no: int) -> _Record | None:
        page = by_page.get(page_no)
        if page is None:
            return None
        # _layout_pieces 已含标题文本，检测用它一份即可（与旧行为一致）。
        return _layout_titles(page), _layout_pieces(page), _layout_ordered(page)

    return _scan_pages(sorted(by_page), record_of)


def _block_level(raw: Any, text: str) -> int:
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return _level(text)


def _keep_heading(text: str, seen: set[str]) -> str:
    """同一句话只留第一次。目录二字和版权行不收。"""
    title = re.sub(r"\s+", " ", (text or "").strip())
    key = re.sub(r"\s+", "", title)
    if not key or key in seen or _is_toc_title(title) or _is_publisher_noise(title):
        return ""
    seen.add(key)
    return title


def _toc_page_numbers(book: Path) -> list[int]:
    """印着目录的那几页。只用来跳过，不从那几页的正文行拆条目。"""
    if _ir_page_nos(book):
        pages, _entries = _from_ir_pages(book)
        return pages
    if load_layout(book):
        pages, _entries = _from_layout(book)
        return pages
    return []


def _collect_ir_headings(book: Path, skip: set[int]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for page_no in _ir_page_nos(book):
        if page_no in skip:
            continue
        page = load_page_json(book, page_no)
        if not page:
            continue
        printed = _chrome_print(page)
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "") != "title":
                continue
            if str(block.get("kind") or "") == "box" or _is_derived(block):
                continue
            title = _keep_heading(str(block.get("text") or ""), seen)
            if not title:
                continue
            entries.append({
                "level": _block_level(block.get("level"), title),
                "title": title,
                "print_page": printed,
                "pdf_page": page_no,
            })
    return entries


def _collect_layout_headings(book: Path, skip: set[int]) -> list[dict[str, Any]]:
    layout = load_layout(book) or {}
    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for page in layout.get("pdf_info") or []:
        if not isinstance(page, dict):
            continue
        page_no = int(page.get("page_idx") or 0) + 1
        if page_no in skip:
            continue
        for block in page.get("para_blocks") or []:
            if not isinstance(block, dict) or str(block.get("type") or "") != "title":
                continue
            title = _keep_heading(_join_text(block.get("lines")), seen)
            if not title:
                continue
            entries.append({
                "level": _block_level(block.get("level"), title),
                "title": title,
                "print_page": None,
                "pdf_page": page_no,
            })
    return entries


# 比对标题只看字：空白、标点、引号括号都去掉，大小写不分。
_KEY_DROP = re.compile(
    r"[\s\u3000·•．.。、,，:：;；!！?？\"'“”‘’()（）\[\]【】《》<>〈〉—–\-_*＊/／|｜~～]+"
)
# 包含关系只在短的一方至少这么多字时才算同一个标题，免得「总结」撞上任何长标题。
_KEY_MIN = 4
# 印刷页 + 偏移那一页没比到时，再看的相邻页（章首页、跨页标题）。
_NEAR_PAGES = (-1, 1, 2)
# 没有印刷页、后面也没有定了页的行时，往后找这么多页。
_SEARCH_TAIL = 3
# 没有偏移时，至少这么多行的「正文页 - 印刷页」一致才采信。
_VOTE_MIN = 2


def _title_key(text: str) -> str:
    return _KEY_DROP.sub("", str(text or "")).lower()


def _same_title(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    return min(len(left), len(right)) >= _KEY_MIN and (left in right or right in left)


def _titles_by_page(book: Path, skip: set[int]) -> dict[int, list[dict[str, Any]]]:
    """每页的标题块（原字、比对键、该页印刷页码）。目录页、框里的、派生的不要。"""
    out: dict[int, list[dict[str, Any]]] = {}
    for page_no in _ir_page_nos(book):
        if page_no in skip:
            continue
        page = load_page_json(book, page_no)
        if not page:
            continue
        printed = _chrome_print(page)
        rows: list[dict[str, Any]] = []
        for block in page.get("blocks") or []:
            if not isinstance(block, dict) or str(block.get("type") or "") != "title":
                continue
            if str(block.get("kind") or "") == "box" or _is_derived(block):
                continue
            text = re.sub(r"\s+", " ", str(block.get("text") or "").strip())
            key = _title_key(text)
            if key:
                rows.append({"text": text, "key": key, "print_page": printed})
        if rows:
            out[page_no] = rows
    return out


def _find_page(titles: dict[int, list[dict[str, Any]]], key: str, lo: int, hi: int) -> int:
    for page_no in range(max(lo, 1), hi + 1):
        if any(_same_title(key, row["key"]) for row in titles.get(page_no, ())):
            return page_no
    return 0


def _skeleton_rows(doc: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in doc.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        label = re.sub(r"\s+", " ", str(entry.get("label") or "").strip())
        if not label:
            continue
        rows.append({
            "title": label,
            "key": _title_key(label),
            "level": max(_entry_level(entry.get("level"), title=label), 0),
            "is_chapter": bool(entry.get("is_chapter")),
            "print_page": ir_print_toc._int(entry.get("print_page")),
            "needs_review": bool(entry.get("needs_review")),
            "pdf_page": 0,
            "page_from": "",
        })
    return rows


def _vote_offset(rows: list[dict[str, Any]], titles: dict[int, list[dict[str, Any]]]) -> int | None:
    """toc.print 没给偏移时：找得到原字的行，正文页 - 印刷页 取票数最多的。"""
    votes: dict[int, int] = {}
    pages = sorted(titles)
    for row in rows:
        if row["print_page"] is None:
            continue
        hit = _find_page(titles, row["key"], pages[0], pages[-1]) if pages else 0
        if hit:
            diff = hit - row["print_page"]
            votes[diff] = votes.get(diff, 0) + 1
    if not votes:
        return None
    diff, count = max(votes.items(), key=lambda kv: kv[1])
    return diff if count >= _VOTE_MIN else None


def _place_rows(
    rows: list[dict[str, Any]],
    titles: dict[int, list[dict[str, Any]]],
    offset: int | None,
    first_body: int,
) -> None:
    """老线定页的顺序：正文里比到原字的页 → 印刷页 + 偏移 → 没印刷页的看前后行。"""
    for row in rows:
        if row["print_page"] is None or offset is None:
            continue
        target = row["print_page"] + offset
        if _find_page(titles, row["key"], target, target):
            row["pdf_page"], row["page_from"] = target, "body"
            continue
        near = next(
            (target + d for d in _NEAR_PAGES if _find_page(titles, row["key"], target + d, target + d)),
            0,
        )
        if near:
            row["pdf_page"], row["page_from"] = near, "near"
        elif target > 0:
            row["pdf_page"], row["page_from"] = target, "offset"
    for index, row in enumerate(rows):
        if row["pdf_page"]:
            continue
        before = max((r["pdf_page"] for r in rows[:index] if r["pdf_page"]), default=0)
        after = next((r["pdf_page"] for r in rows[index + 1 :] if r["pdf_page"]), 0)
        lo = before or first_body
        hit = _find_page(titles, row["key"], lo, after or lo + _SEARCH_TAIL)
        heads_next = index + 1 < len(rows) and rows[index + 1]["level"] > row["level"]
        if hit:
            row["pdf_page"], row["page_from"] = hit, "search"
        elif heads_next and after:
            row["pdf_page"], row["page_from"] = after, "next"
        elif before:
            row["pdf_page"], row["page_from"] = before, "prev"
        elif after:
            row["pdf_page"], row["page_from"] = after, "next"


def _body_kids(
    rows: list[dict[str, Any]],
    titles: dict[int, list[dict[str, Any]]],
    last_page: int,
) -> list[list[dict[str, Any]]]:
    """老线挂细目：从这一行的页到下一行开始之前，没进骨架的正文标题挂在这一行下面。"""
    seen: set[str] = set()
    out: list[list[dict[str, Any]]] = []
    for index, row in enumerate(rows):
        kids: list[dict[str, Any]] = []
        start = row["pdf_page"]
        end = next((r["pdf_page"] for r in rows[index + 1 :] if r["pdf_page"]), last_page + 1)
        for page_no in range(start, end) if start else ():
            for item in titles.get(page_no, ()):
                if any(_same_title(item["key"], other["key"]) for other in rows):
                    continue
                title = _keep_heading(item["text"], seen)
                if not title:
                    continue
                kids.append({
                    "level": row["level"] + 1,
                    "title": title,
                    "print_page": item["print_page"],
                    "pdf_page": page_no,
                })
        out.append(kids)
    return out


def _print_skeleton_toc(book: Path, doc: dict[str, Any], print_sha: str) -> dict[str, Any]:
    """印刷目录当骨架：toc.print 的每一行原样当条目，正文只用来定页和挂细目。"""
    toc_pages = sorted(p for p in (ir_print_toc._int(x) for x in doc.get("toc_pages") or []) if p)
    titles = _titles_by_page(book, set(toc_pages))
    rows = _skeleton_rows(doc)
    offset = ir_print_toc._int(doc.get("page_offset"))
    if offset is None:
        offset = _vote_offset(rows, titles)
    first_body = (toc_pages[-1] + 1) if toc_pages else 1
    _place_rows(rows, titles, offset, first_body)
    pages = _ir_page_nos(book)
    kids = _body_kids(rows, titles, pages[-1] if pages else 0)
    entries: list[dict[str, Any]] = []
    for row, below in zip(rows, kids):
        entry = _decorate({
            "level": row["level"],
            "title": row["title"],
            "print_page": row["print_page"],
            "pdf_page": row["pdf_page"] or None,
        })
        entry["is_chapter"] = row["is_chapter"]
        entry["page_from"] = row["page_from"] or None
        if row["needs_review"]:
            entry["needs_review"] = True
        entries.append(entry)
        for kid in below:
            entry = _decorate(kid)
            entry["is_chapter"] = False
            entry["from_body"] = True
            entries.append(entry)
    return {
        "book": book.name,
        "source": "ir_toc_print",
        "reviewed": False,
        "print_sha": print_sha,
        "page_offset": offset,
        "toc_pages": toc_pages,
        "entries": entries,
        "unresolved": sum(1 for row in entries if not row.get("pdf_page")),
    }


def build_toc(book: Path, front_pages: int = _FRONT_PAGES) -> dict[str, Any]:
    """有印刷目录（toc.print）就拿它当骨架；没有才从中间页的标题块收到最小一级。"""
    del front_pages
    if not load_layout(book) and not _ir_page_nos(book):
        raise FileNotFoundError(f"missing layout.json under {book / 'mineru'}")
    doc, print_sha = ir_print_toc._read_print(book)
    if doc and doc.get("found") and doc.get("entries") and _ir_page_nos(book):
        toc = _print_skeleton_toc(book, doc, print_sha)
        if toc["entries"]:
            return toc
    skip = set(_toc_page_numbers(book))
    if _ir_page_nos(book):
        raw_entries = _collect_ir_headings(book, skip)
    else:
        raw_entries = _collect_layout_headings(book, skip)
    entries = [_decorate(row) for row in raw_entries]
    return {
        "book": book.name,
        "source": "ir_toc_headings",
        "reviewed": False,
        "toc_pages": sorted(skip),
        "entries": entries,
        "unresolved": sum(1 for row in entries if not row.get("pdf_page")),
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


def _toc_path(book: Path) -> Path:
    return book / "ir" / "toc.json"


def _read_saved_toc(book: Path) -> dict[str, Any] | None:
    path = _toc_path(book)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_toc(book: Path, toc: dict[str, Any]) -> None:
    out = _toc_path(book)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"toc.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    tmp.write_text(json.dumps(toc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(out)


def _page_titles(book: Path, page_no: Any, cache: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    try:
        idx = int(page_no)
    except (TypeError, ValueError):
        return []
    if idx in cache:
        return cache[idx]
    page = load_page_json(book, idx) or {}
    found: list[dict[str, Any]] = []
    for block in page.get("blocks") or []:
        if not isinstance(block, dict) or str(block.get("type") or "") != "title":
            continue
        if str(block.get("kind") or "") == "box" or _is_derived(block):
            continue
        text = str(block.get("text") or "").strip()
        if text:
            found.append({"level": block.get("level"), "text": text[:80]})
    cache[idx] = found
    return found


def _review_prompt() -> str:
    path = Path(__file__).resolve().parent.parent / "prompts" / "ir_toc_review.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        "对照每一项所在页的标题块审核目录。"
        "练习、习题、例题、A组、B组、以及练习指令删掉。"
        "错字和层级可以改。只输出一个 JSON 对象，键为 items。"
    )


def _apply_review_edits(entries: list[dict[str, Any]], edits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按页码加标题对上审核结果。没点名的留下。drop 删掉，rename 改字和层级。"""
    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        try:
            page_no = int(edit.get("pdf_page") or 0)
        except (TypeError, ValueError):
            continue
        key = (page_no, re.sub(r"\s+", "", str(edit.get("title") or "")))
        if key[1]:
            by_key[key] = edit
    kept: list[dict[str, Any]] = []
    for row in entries:
        try:
            page_no = int(row.get("pdf_page") or 0)
        except (TypeError, ValueError):
            page_no = 0
        edit = by_key.get((page_no, re.sub(r"\s+", "", str(row.get("title") or ""))))
        if edit is None:
            kept.append(row)
            continue
        action = str(edit.get("action") or "keep").strip().lower()
        if action == "drop":
            continue
        if action != "rename" and edit.get("level") is None:
            kept.append(row)
            continue
        changed = dict(row)
        if action == "rename":
            label = str(edit.get("label") or row.get("title") or "").strip()
            if label:
                changed["title"] = label
                changed["label"] = label
        if edit.get("level") is not None:
            try:
                changed["level"] = int(edit["level"])
            except (TypeError, ValueError):
                pass
        lv = changed.get("level")
        if lv is not None:
            changed["is_chapter"] = int(lv) == 0
        kept.append(changed)
    return kept


def apply_layout_toc_file(
    book_id: str,
    layout_toc_path: str | Path,
    root: str = "",
    source: str = "layout_toc_import",
    force: bool = False,
) -> dict[str, Any]:
    """Replace ``ir/toc.json`` from a JSON file (e.g. Composer layout export).

    Each row needs ``title`` and ``pdf_page``; ``level`` may be ``0`` for chapters.
    ``print_page`` is filled from human ``toc.json`` when titles match.
    """
    sid = safe_book_id(book_id)
    book = book_dir(sid, root)
    saved = _read_saved_toc(book)
    if saved and saved.get("reviewed") and not force:
        return {
            "ok": True,
            "book_id": sid,
            "skipped": True,
            "reason": "reviewed",
            "entries": len(saved.get("entries") or []),
        }
    path = Path(layout_toc_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("entries") if isinstance(data.get("entries"), list) else []
    ref = _human_print_page_ref(book)
    decorated: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("label") or "").strip()
        if not title:
            continue
        try:
            pdf = int(row.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf = 0
        entry = _decorate({"title": title, "level": row.get("level"), "pdf_page": pdf})
        pp = ref.get((_norm_toc_title(title), pdf)) or ref.get((_norm_toc_title(title), None))
        if pp is not None:
            entry["print_page"] = pp
        decorated.append(entry)
    toc = {
        "book": sid,
        "source": source,
        "reviewed": False,
        "toc_pages": list(saved.get("toc_pages") or data.get("toc_pages") or []),
        "entries": decorated,
        "unresolved": sum(1 for row in decorated if not row.get("pdf_page")),
    }
    _write_toc(book, toc)
    return {
        "ok": True,
        "book_id": sid,
        "apply": "layout_import",
        "source": source,
        "entries": len(decorated),
        "unresolved_pages": toc["unresolved"],
    }


def _ask_review(prompt: str, payload: list[dict[str, Any]], job_id: str) -> list[dict[str, Any]]:
    from app.json_util import extract_json_object
    from app.sdk_runner import _prompt_sdk

    user = json.dumps({"items": payload}, ensure_ascii=False)
    text, _sdk_id = _prompt_sdk(
        f"{prompt.rstrip()}\n\n{user}",
        conversation_id="",
        job_id=job_id,
        mode="ask",
        workspace_tools=False,
    )
    data = extract_json_object(text or "")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("review JSON has no items")
    return [row for row in items if isinstance(row, dict)]


def review_toc(book_id: str, root: str = "", force: bool = False, batch: int = 8) -> dict[str, Any]:
    """把草稿逐条交给知识库 Agent。证据是该页中间文件里的标题块。审完写回同一份目录。"""
    sid = safe_book_id(book_id)
    book = book_dir(sid, root)
    saved = _read_saved_toc(book)
    if saved and saved.get("reviewed") and not force:
        return {
            "ok": True,
            "book_id": sid,
            "apply": "review",
            "skipped": True,
            "reason": "reviewed",
            "entries": len(saved.get("entries") or []),
        }
    toc = saved if saved and isinstance(saved.get("entries"), list) else build_toc(book)
    entries = [row for row in (toc.get("entries") or []) if isinstance(row, dict)]
    prompt = _review_prompt()
    cache: dict[int, list[dict[str, Any]]] = {}
    edits: list[dict[str, Any]] = []
    step = max(int(batch or 8), 1)
    for start in range(0, len(entries), step):
        chunk = entries[start : start + step]
        payload = []
        for row in chunk:
            payload.append({
                "pdf_page": row.get("pdf_page"),
                "level": row.get("level"),
                "title": row.get("title"),
                "page_titles": _page_titles(book, row.get("pdf_page"), cache),
            })
        edits.extend(_ask_review(prompt, payload, job_id=""))
    reviewed_entries = _apply_review_edits(entries, edits)
    out = dict(toc)
    out["entries"] = reviewed_entries
    out["reviewed"] = True
    out["source"] = "ir_toc_review"
    out["unresolved"] = sum(1 for row in reviewed_entries if not row.get("pdf_page"))
    _write_toc(book, out)
    return {
        "ok": True,
        "book_id": sid,
        "apply": "review",
        "entries": len(reviewed_entries),
        "dropped": len(entries) - len(reviewed_entries),
        "reviewed": True,
    }


def collect_toc_manifest(book: Path) -> dict[str, Any]:
    """Compile a title list from intermediate pages. This is the polish input."""
    if not load_layout(book) and not _ir_page_nos(book):
        raise FileNotFoundError(f"missing layout.json under {book / 'mineru'}")
    skip = set(_toc_page_numbers(book))
    if _ir_page_nos(book):
        raw = _collect_ir_headings(book, skip)
        origin = "ir_pages"
    else:
        raw = _collect_layout_headings(book, skip)
        origin = "layout"
    items = [
        {
            "level": row.get("level"),
            "title": row.get("title"),
            "pdf_page": row.get("pdf_page"),
        }
        for row in raw
    ]
    return {
        "origin": origin,
        "toc_pages": sorted(skip),
        "items": items,
        "raw": raw,
    }


# 练习、习题、例题、A/B 组由规则删掉，不交给模型改写。
_RULE_DROP_HEADING = re.compile(r"^\s*(?:练习|习题|例题|例\s*\d|[AB]组)")


def _rule_drops_heading(title: str) -> bool:
    return _RULE_DROP_HEADING.match(title or "") is not None


def _polish_prompt() -> str:
    path = Path(__file__).resolve().parent.parent / "prompts" / "ir_toc_polish.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        "下面 JSON 的 items 是从教材中间页标题块编译出的清单。不要读其它文件。\n"
        "你只判断每一条留还是丢，以及层级。不要输出 title，不要输出页码，不要新编 id。\n"
        "action 只能是 keep 或 drop。level 只能是 0、1 或 2。0 是章，1 是节，2 是更小的标题。\n"
        "练习、习题、例题已经事先删掉。保留章、节和必要的小标题。\n"
        "只输出一个 JSON 对象："
        '{"decisions":[{"id":0,"action":"keep","level":0}]}'
    )


def _decision_rows(text: str) -> list[dict[str, Any]]:
    from app.json_util import extract_json_object

    data = extract_json_object(text or "")
    rows = data.get("decisions")
    if not isinstance(rows, list):
        raise ValueError("polish JSON has no decisions")
    return [row for row in rows if isinstance(row, dict)]


def apply_polish_decisions(
    items: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy title and file page from the manifest. The model only keeps, drops, or sets level.

    A decision is ignored when its id is unknown, its action is not keep/drop,
    or its level is outside 0, 1, 2. An ignored row stays as compiled.
    """
    chosen: dict[int, tuple[str, int | None]] = {}
    for row in decisions:
        try:
            item_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        action = str(row.get("action") or "").strip().lower()
        if action not in {"keep", "drop"}:
            continue
        level: int | None = None
        if row.get("level") is not None and str(row.get("level")).strip() != "":
            try:
                level = int(row.get("level"))
            except (TypeError, ValueError):
                continue
            if level not in (0, 1, 2):
                continue
        chosen[item_id] = (action, level)
    out: list[dict[str, Any]] = []
    for row in items:
        try:
            item_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        title = str(row.get("title") or "").strip()
        if not title or _rule_drops_heading(title):
            continue
        try:
            pdf = int(row.get("pdf_page") or 0)
        except (TypeError, ValueError):
            pdf = 0
        decision = chosen.get(item_id)
        if decision is not None and decision[0] == "drop":
            continue
        level = decision[1] if decision is not None and decision[1] is not None else row.get("level")
        out.append({"title": title, "level": level, "pdf_page": pdf})
    return out


def _attach_print_pages(polished: list[dict[str, Any]], raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ref: dict[tuple[str, int | None], Any] = {}
    for row in raw:
        title = str(row.get("title") or "")
        try:
            pdf = int(row.get("pdf_page") or 0) or None
        except (TypeError, ValueError):
            pdf = None
        ref[(_norm_toc_title(title), pdf)] = row.get("print_page")
        ref.setdefault((_norm_toc_title(title), None), row.get("print_page"))
    out: list[dict[str, Any]] = []
    for row in polished:
        entry = _decorate(row)
        key = _norm_toc_title(str(entry.get("title") or ""))
        try:
            pdf = int(entry.get("pdf_page") or 0) or None
        except (TypeError, ValueError):
            pdf = None
        printed = ref.get((key, pdf))
        if printed is None:
            printed = ref.get((key, None))
        if printed is not None and str(printed).strip():
            entry["print_page"] = str(printed).strip()
        out.append(entry)
    return out


def polish_toc(book_id: str, root: str = "") -> dict[str, Any]:
    """Delete ir/toc.json, compile the title manifest, polish once, write it back.

    The model is the knowledge-base agent's Composer 2.5 Fast. The prompt
    contains only the manifest, so the agent does not open the raw layout.
    """
    sid = safe_book_id(book_id)
    book = book_dir(sid, root)
    started = time.perf_counter()
    toc_path = _toc_path(book)
    backup_path = book.parent.parent / "logs" / f"toc-backup-{sid}.json"
    if toc_path.is_file():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(toc_path.read_bytes())
        toc_path.unlink()
    deleted_s = round(time.perf_counter() - started, 3)

    collect_started = time.perf_counter()
    manifest = collect_toc_manifest(book)
    collect_s = round(time.perf_counter() - collect_started, 3)

    from app.sdk_runner import _prompt_sdk

    numbered = []
    for item_id, row in enumerate(manifest["items"]):
        numbered.append({
            "id": item_id,
            "level": row.get("level"),
            "title": row.get("title"),
            "pdf_page": row.get("pdf_page"),
            "print_page": (manifest["raw"][item_id].get("print_page") if item_id < len(manifest["raw"]) else None),
        })
    ask = [
        {"id": row["id"], "level": row["level"], "title": row["title"]}
        for row in numbered
        if not _rule_drops_heading(str(row.get("title") or ""))
    ]
    payload = json.dumps({"items": ask}, ensure_ascii=False)
    polish_started = time.perf_counter()
    try:
        text, sdk_id = _prompt_sdk(
            f"{_polish_prompt().rstrip()}\n\n{payload}",
            conversation_id="",
            job_id="",
            mode="ask",
            model="composer-2.5",
            model_params=[{"id": "fast", "value": "true"}],
            workspace_tools=False,
        )
        polish_s = round(time.perf_counter() - polish_started, 3)
        polished = apply_polish_decisions(numbered, _decision_rows(text or ""))
        if not polished:
            raise ValueError("polish kept nothing")
    except Exception:
        if backup_path.is_file() and not toc_path.is_file():
            toc_path.write_bytes(backup_path.read_bytes())
        raise
    entries = _attach_print_pages(polished, manifest["raw"])
    toc = {
        "book": sid,
        "source": "ir_toc_polish",
        "model": "composer-2.5",
        "fast": True,
        "reviewed": False,
        "manifest_origin": manifest["origin"],
        "manifest_count": len(manifest["items"]),
        "ruled_out": len(numbered) - len(ask),
        "sent": len(ask),
        "toc_pages": manifest["toc_pages"],
        "entries": entries,
        "unresolved": sum(1 for row in entries if not row.get("pdf_page")),
        "timing_s": {
            "delete": deleted_s,
            "collect": collect_s,
            "polish": polish_s,
            "total": round(time.perf_counter() - started, 3),
        },
    }
    _write_toc(book, toc)
    total_s = toc["timing_s"]["total"]
    report = {
        "ok": True,
        "book_id": sid,
        "apply": "polish",
        "source": toc["source"],
        "model": "composer-2.5",
        "fast": True,
        "sdk_id": sdk_id,
        "deleted": True,
        "backup": str(backup_path) if backup_path.is_file() else "",
        "manifest_origin": manifest["origin"],
        "manifest_count": len(manifest["items"]),
        "ruled_out": len(numbered) - len(ask),
        "sent": len(ask),
        "entries": len(entries),
        "timing_s": {**toc["timing_s"], "total": total_s},
    }
    log_path = book.parent.parent / "logs" / "toc-polish-composer25-fast.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def _keep_saved(saved: dict[str, Any] | None, toc: dict[str, Any]) -> bool:
    """审过的不覆盖；除非新稿是印刷目录骨架、而审的不是同一份 toc.print。"""
    if not saved or not saved.get("reviewed"):
        return False
    if toc.get("source") != "ir_toc_print":
        return True
    return saved.get("print_sha") == toc.get("print_sha")


def _backup_replaced(book: Path, saved: dict[str, Any] | None, toc: dict[str, Any]) -> None:
    """换了来源（收标题块 / 审过 / 润色过 → 骨架）时，旧文件留一份到 polish 用的同一位置。"""
    if not saved or saved.get("source") == toc.get("source"):
        return
    backup = book.parent.parent / "logs" / f"toc-backup-{book.name}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(saved, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def ir_toc(book_id: str, root: str = "", apply: str = "scan", force: bool = False) -> dict[str, Any]:
    raw = (book_id or "").strip()
    mode = (apply or "scan").strip().lower()
    if raw in ("", "*"):
        rows = [ir_toc(bid, root, mode, force=force) for bid in _book_ids(root)]
        failed = [r.get("book_id") for r in rows if not r.get("ok")]
        return {
            "ok": not failed,
            "book_id": "*",
            "apply": mode,
            "books": len(rows),
            "entries": sum(int(r.get("entries") or 0) for r in rows),
            "empty": sum(1 for r in rows if int(r.get("entries") or 0) == 0),
            "skipped": sum(1 for r in rows if r.get("skipped")),
            "failed": failed,
        }
    sid = safe_book_id(book_id)
    book = book_dir(sid, root)
    if mode == "review":
        return review_toc(sid, root, force=force)
    if mode == "polish":
        return polish_toc(sid, root)
    saved = _read_saved_toc(book)
    toc = build_toc(book)
    if mode == "fix" and not force and _keep_saved(saved, toc):
        return {
            "ok": True,
            "book_id": sid,
            "apply": mode,
            "skipped": True,
            "reason": "reviewed",
            "toc_pages": saved.get("toc_pages") or [],
            "entries": len(saved.get("entries") or []),
            "unresolved_pages": saved.get("unresolved") or 0,
        }
    diff = _diff_reference(book, toc)
    if mode == "fix":
        _backup_replaced(book, saved, toc)
        _write_toc(book, toc)
    return {
        "ok": True,
        "book_id": sid,
        "apply": mode,
        "source": toc["source"],
        "toc_pages": toc["toc_pages"],
        "entries": len(toc["entries"]),
        "from_body": sum(1 for row in toc["entries"] if row.get("from_body")),
        "unresolved_pages": toc["unresolved"],
        **diff,
    }
