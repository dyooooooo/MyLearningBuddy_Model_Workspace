"""Official MinerU markdown line.

Library only — not in manifest.yaml. No ``app.*`` imports, no ``v2md_*``.
Reads ``mineru/full.md`` plus the official v1 content list (``page_idx``).
Does not write ``pages/``, ``mid/``, ``v2md/``, or ``mineru/full.md``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_LOOSE_SKIP = frozenset("$^{}#*`\\ \t\n\r")
_TAG = re.compile(
    r"</?(?:table|thead|tbody|tr|td|th|sup|sub|br|img|b|i|em|strong|"
    r"div|span|p|ul|ol|li|a)\b[^>]*/?>",
    re.I,
)
_MD_IMG = re.compile(r"(!\[[^\]]*\]\()(images/[^)]+)(\))")
_HTML_IMG = re.compile(r"""(\bsrc=["'])images/([^"']+)(["'])""", re.I)
_CHROME = frozenset(
    {"header", "footer", "page_header", "page_footer", "page_number", "page-number", "page_footnote"}
)
_BODY = frozenset(
    {
        "text",
        "title",
        "equation",
        "interline_equation",
        "table",
        "image",
        "chart",
        "ref_text",
        "aside_text",
        "code",
    }
)
_MIN_NEEDLE = 8
_MIN_UNIQUE = 4
_MIN_CELL = 6
_TINY_SPAN = 8
_MIN_HOLD = 24
_MAX_HOLD = 4000
_IMPRINT_TAIL = ("出版社",)


def safe_book_id(book_id: str) -> str:
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    return sid


def book_dir(book_id: str, root: str = "") -> Path:
    sid = safe_book_id(book_id)
    base = Path(root).resolve() if root else Path.cwd()
    if (base / "textbooks").is_dir() and not (base / sid).is_dir():
        return base / "textbooks" / sid
    return base / sid


def asset_url(book_id: str, path: str) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if not raw:
        return ""
    if raw.startswith("/api/") or raw.startswith("http://") or raw.startswith("https://"):
        return raw
    name = raw.rsplit("/", 1)[-1]
    if not name:
        return ""
    key = f"kb/pdfs/{safe_book_id(book_id)}/assets/mineru/{name}"
    return f"/api/v1/kb/proxy/asset?key={quote(key, safe='')}"


def rewrite_image_urls(markdown: str, book_id: str) -> str:
    if not markdown:
        return markdown

    def _md(match: re.Match[str]) -> str:
        url = asset_url(book_id, match.group(2))
        return match.group(0) if not url else f"{match.group(1)}{url}{match.group(3)}"

    def _html(match: re.Match[str]) -> str:
        url = asset_url(book_id, match.group(2))
        return match.group(0) if not url else f"{match.group(1)}{url}{match.group(3)}"

    return _HTML_IMG.sub(_html, _MD_IMG.sub(_md, markdown))


def find_v1_path(book: Path) -> Path | None:
    folder = book / "mineru"
    if not folder.is_dir():
        return None
    hits = [
        path
        for path in sorted(folder.glob("*_content_list.json"))
        if not path.name.endswith("_content_list_v2.json")
    ]
    return hits[0] if hits else None


def load_v1_pages(book: Path) -> dict[int, list[dict[str, Any]]]:
    path = find_v1_path(book)
    if path is None:
        raise FileNotFoundError(f"missing content_list under {book / 'mineru'}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("content_list is empty")
    grouped: dict[int, list[dict[str, Any]]] = {}
    for block in data:
        if not isinstance(block, dict):
            continue
        try:
            page_no = int(block.get("page_idx") or 0) + 1
        except (TypeError, ValueError):
            page_no = 1
        if page_no < 1:
            page_no = 1
        grouped.setdefault(page_no, []).append(block)
    return dict(sorted(grouped.items()))


def loose_text(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch not in _LOOSE_SKIP)


def _plain_field(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_plain_field(item) for item in value]
        return "".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "caption"):
            got = _plain_field(value.get(key))
            if got:
                return got
    return ""


def _is_hash_needle(plain: str) -> bool:
    text = plain.lower()
    if "." in text:
        text = text.rsplit(".", 1)[0]
    return len(text) >= 24 and all(ch in "0123456789abcdef" for ch in text[:24])


def _is_imprint(plain: str) -> bool:
    return any(plain.endswith(tail) for tail in _IMPRINT_TAIL)


def _table_cells(html: object) -> list[str]:
    raw = str(html or "")
    if not raw:
        return []
    text = _TAG.sub("\n", raw)
    cells: list[str] = []
    for part in text.splitlines():
        part = part.strip()
        if len(loose_text(part)) >= _MIN_CELL:
            cells.append(part)
    return cells


def _has_long_body(blocks: list[dict[str, Any]]) -> bool:
    for needle in body_needles(blocks):
        plain = loose_text(needle)
        if len(plain) >= _MIN_NEEDLE and not _is_hash_needle(plain) and not _is_imprint(plain):
            return True
    return False


def body_needles(blocks: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    short_raw: list[str] = []
    for block in blocks:
        kind = str(block.get("type") or "").lower()
        if kind in _CHROME or kind not in _BODY:
            continue
        if kind in {"image", "chart"}:
            cap = _plain_field(block.get("image_caption") or block.get("chart_caption"))
            if cap:
                out.append(cap)
            path = str(block.get("img_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
            if path:
                out.append(path)
            continue
        if kind == "table":
            cap = _plain_field(block.get("table_caption"))
            if cap:
                out.append(cap)
            out.extend(_table_cells(block.get("table_body")))
            path = str(block.get("img_path") or "").replace("\\", "/").rsplit("/", 1)[-1]
            if path:
                out.append(path)
            continue
        if kind == "code":
            body = _plain_field(block.get("code_body") or block.get("text"))
            if body:
                out.append(body)
            continue
        text = _plain_field(block.get("text"))
        if text:
            out.append(text)
            if kind in {"text", "title", "aside_text", "ref_text"}:
                short_raw.append(text)
    if short_raw and all(len(loose_text(part)) < _MIN_NEEDLE for part in short_raw):
        joined = "".join(short_raw)
        if len(loose_text(joined)) >= _MIN_NEEDLE:
            out.append(joined)
    return out


def _loose_index(full: str) -> tuple[str, list[int]]:
    loose: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(full):
        if ch in _LOOSE_SKIP:
            continue
        loose.append(ch)
        idx.append(i)
    return "".join(loose), idx


def _line_start(full: str, pos: int) -> int:
    if pos <= 0:
        return 0
    if pos >= len(full):
        return len(full)
    nl = full.rfind("\n", 0, pos)
    return 0 if nl < 0 else nl + 1


def _earliest(blob: str, cursor: int, plains: list[str], min_len: int) -> int | None:
    best: int | None = None
    for plain in plains:
        if len(plain) < min_len:
            continue
        pos = blob.find(plain, cursor)
        if pos < 0 and len(plain) > 16:
            pref = plain[:16]
            if blob[cursor:].count(pref) == 1:
                pos = blob.find(pref, cursor)
        if pos >= cursor and (best is None or pos < best):
            best = pos
    return best


def _loose_at(idx: list[int], full_pos: int) -> int:
    for i, pos in enumerate(idx):
        if pos >= full_pos:
            return i
    return len(idx)


def _last_match_end(blob: str, start: int, limit: int, plains: list[str]) -> int:
    last = start
    for plain in plains:
        if len(plain) < 4:
            continue
        pos = start
        while True:
            hit = blob.find(plain, pos)
            if hit < 0 or hit >= limit:
                break
            last = max(last, hit + len(plain))
            pos = hit + 1
    return last


def _drop_tiny_same_line(full: str, starts: dict[int, int]) -> dict[int, int]:
    nums = sorted(starts)
    drop: set[int] = set()
    for a, b in zip(nums, nums[1:]):
        if starts[b] - starts[a] < _TINY_SPAN and _line_start(full, starts[a]) == _line_start(
            full, starts[b]
        ):
            drop.add(a)
    for n in drop:
        del starts[n]
    return starts


def _hold_cursor(blob: str, start: int, plains: list[str]) -> int:
    held = start + 1
    for plain in plains:
        if len(plain) < _MIN_HOLD or _is_hash_needle(plain) or _is_imprint(plain):
            continue
        hit = blob.find(plain, start)
        matched = len(plain)
        if hit < 0 and len(plain) > 16:
            hit = blob.find(plain[:16], start)
            matched = 16
        if hit >= start and hit - start <= _MAX_HOLD:
            held = max(held, hit + matched)
    return held


def _unique_short(blob: str, cursor: int, plains: list[str]) -> int | None:
    rest = blob[cursor:]
    best: int | None = None
    for plain in plains:
        if not (_MIN_UNIQUE <= len(plain) < _MIN_NEEDLE):
            continue
        if _is_imprint(plain):
            continue
        if rest.count(plain) != 1:
            continue
        pos = blob.find(plain, cursor)
        if pos >= cursor and (best is None or pos < best):
            best = pos
    return best


def page_starts(full: str, pages: dict[int, list[dict[str, Any]]]) -> dict[int, int]:
    """Map 1-based page numbers to offsets in official full.md.

    Pages are walked in order. Each page is searched only at or after the
    previous hit (no look-back). Among that page's needles, the earliest
    remaining hit wins, so a late image hash cannot jump past an earlier
    heading on the same page.
    """
    blob, idx = _loose_index(full)
    if not blob or not idx:
        return {}
    cursor = 0
    starts: dict[int, int] = {}
    for n in sorted(pages):
        plains = [loose_text(needle) for needle in body_needles(pages[n])]
        plains = [p for p in plains if p and not _is_imprint(p)]
        found = _earliest(blob, cursor, plains, _MIN_NEEDLE)
        if found is None:
            found = _unique_short(blob, cursor, plains)
        if found is None:
            continue
        starts[n] = idx[found]
        cursor = _hold_cursor(blob, found, plains)
    return _drop_tiny_same_line(full, starts)


def find_official_span(
    full: str,
    pages: dict[int, list[dict[str, Any]]],
    page_no: int,
    starts: dict[int, int] | None = None,
) -> tuple[int, int] | None:
    if not full or page_no < 1:
        return None
    found = starts if starts is not None else page_starts(full, pages)
    if page_no not in found:
        return None
    raw_left = found[page_no]
    later = [found[n] for n in found if n > page_no and found[n] > raw_left]
    raw_right = min(later) if later else len(full)
    left = _line_start(full, raw_left)
    prevs = [found[n] for n in found if n < page_no]
    if prevs:
        prev_raw = max(prevs)
        if prev_raw < raw_left and _line_start(full, prev_raw) == left:
            left = raw_left
    if later:
        right = _line_start(full, raw_right)
        if right <= left:
            right = raw_right
    else:
        right = len(full)
    next_anchored = min((n for n in found if n > page_no and found[n] > raw_left), default=None)
    hole = [
        n
        for n in pages
        if n > page_no and (next_anchored is None or n < next_anchored)
    ]
    if hole and any(_has_long_body(pages[n]) for n in hole):
        blob, idx = _loose_index(full)
        plains = [loose_text(needle) for needle in body_needles(pages[page_no])]
        plains = [p for p in plains if p]
        start_l = _loose_at(idx, raw_left)
        limit_l = _loose_at(idx, raw_right) if later else len(blob)
        last_l = _last_match_end(blob, start_l, limit_l, plains)
        if last_l > start_l:
            end = idx[last_l - 1] + 1 if last_l <= len(idx) else len(full)
            nl = full.find("\n", end)
            if 0 <= nl < raw_right:
                end = nl + 1
            else:
                end = min(end, raw_right)
            if left < end < right:
                right = end
    if right <= left:
        return None
    return left, right
