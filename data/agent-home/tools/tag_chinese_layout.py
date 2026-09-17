from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FOOTNOTE = re.compile(
    r"\$\^\{[^}]+\}\$|\^\{[^}]+\}|[①-⑳]|[₁₂₃₄₅₆₇₈₉₀]|"
    r"(?<![A-Za-z0-9])[（(]\s*\d+\s*[）)]"
)
_YEAR_LEAD = re.compile(r"^\d{3,4}\s*年")
_DATE = re.compile(
    r"^\d{3,4}\s*年"
    r"(?:"
    r"[春夏秋冬]"
    r"|(?:\d{1,2}\s*[、，]\s*)*\d{1,2}\s*月(?:\d{1,2}\s*日)?"
    r")?"
    r"(?:间)?(?:作|初稿|删定|改定|完稿)?"
    r"$"
)
_NOTE_HEAD = re.compile(
    r"^(?:[①-⑳㉑-㉟㊱-㊿⑴-⒇]|\$\^\{[^}]+\}\$|[1-9]\d{0,1}\s*[.)、．])"
)
_AUTHOR = re.compile(r"^(?:〔[^〕]{1,12}〕)?[\u4e00-\u9fffA-Za-z·•]{2,12}$")
_BOOK_SRC = re.compile(r"^《[^》]{1,24}》$")
_UNIT = re.compile(r"^第.+单元")
_LESSON = re.compile(r"^\*?\d{1,2}$")
_IMG = re.compile(r"^!\[[^\]]*\]\([^)]+\)\s*$")
_NOT_TITLE = frozenset(
    {
        "学习提示",
        "单元学习任务",
        "思考与交流",
        "积累与运用",
        "阅读指导",
        "目录",
        "目 录",
        "学习活动",
        "活动提示",
        "学习任务",
        "古诗词诵读",
    }
)
_COVER = frozenset({"语文"})
_NUMBERED_HEAD = re.compile(
    r"^(?:"
    r"[（(]\s*\d+\s*[）)]"
    r"|[1-9]\d{0,1}\s*[\.．、]"
    r"|[一二三四五六七八九十]+、?"
    r"|例\s*\d"
    r"|附[一二三四五六七八九十]"
    r"|任务\s*\d"
    r")"
)
_WRITE_LESSON = re.compile(r"^(?:学写|写人要|议论要|如何做到)")
_SUBTITLE_HEAD = re.compile(r"^[—–\-]{1,2}")
_SKIP_TYPES = frozenset(
    {
        "page_header",
        "page_footer",
        "page_number",
        "page_footnote",
        "header",
        "footer",
        "image",
        "table",
        "discarded",
    }
)
_DIALOGUE_OPEN = frozenset("“「『\"‟")


def is_chinese_textbook(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    subject = str(meta.get("subject") or "").strip().lower()
    category = str(meta.get("category") or "").strip().lower()
    return subject == "chinese" and category in {"textbook", "教材"}


def normalize_line(text: str) -> str:
    out = _FOOTNOTE.sub("", text or "")
    out = re.sub(r"^#{1,6}\s+", "", out)
    out = out.replace("*", "").replace("＊", "")
    out = re.sub(r"\s+", "", out)
    return out.strip()


def toc_work_titles(toc: dict[str, Any] | None) -> set[str]:
    titles: set[str] = set()
    entries = (toc or {}).get("entries") if isinstance(toc, dict) else None
    for row in entries or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label or _LESSON.match(label) or _UNIT.match(label):
            continue
        if int(row.get("level") or 0) < 2 and not row.get("is_chapter"):
            if " / " not in label and "／" not in label:
                continue
        head = re.split(r"\s*[／/]\s*", label.lstrip("*＊").strip(), maxsplit=1)[0]
        head = normalize_line(head)
        if head and head not in _NOT_TITLE and not _LESSON.match(head):
            titles.add(head)
    return titles


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_as_text(x) for x in value]
        return "".join(p for p in parts if p)
    if isinstance(value, dict):
        for key in (
            "content",
            "text",
            "title_content",
            "paragraph_content",
            "page_footnote_content",
        ):
            if key in value:
                got = _as_text(value[key])
                if got:
                    return got
        return ""
    return ""


def block_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, dict):
        for key in ("paragraph_content", "title_content", "text"):
            if key in content:
                raw = content[key]
                if isinstance(raw, list):
                    chunks: list[str] = []
                    for item in raw:
                        piece = _as_text(item)
                        if piece:
                            chunks.append(piece)
                    return "".join(chunks)
                return _as_text(raw)
    text = block.get("text")
    if isinstance(text, str):
        return text
    return _as_text(content)


def block_bbox(block: dict[str, Any]) -> list[float] | None:
    raw = block.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        return [float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])]
    except (TypeError, ValueError):
        return None


def classify_line_boxes(boxes: list[list[float]], page_width: float = 1000.0) -> str:
    if len(boxes) < 2:
        return "kb-verse"
    width = page_width if page_width > 1 else 1000.0
    x0s = [box[0] for box in boxes]
    x1s = [box[2] for box in boxes]
    mids = [(box[0] + box[2]) / 2.0 for box in boxes]
    x0_span = max(x0s) - min(x0s)
    x1_span = max(x1s) - min(x1s)
    mid_span = max(mids) - min(mids)
    if x0_span <= 0.03 * width and x1_span > 0.08 * width:
        return "kb-verse"
    centered = 0
    for box in boxes:
        left = box[0]
        right = width - box[2]
        if abs(left - right) <= 0.12 * width and (box[2] - box[0]) < 0.55 * width:
            centered += 1
    if centered >= max(2, (len(boxes) + 1) // 2):
        return "kb-verse-center"
    if mid_span <= 0.08 * width and x0_span > 0.02 * width:
        return "kb-verse-center"
    return "kb-verse"


def _is_narrow(box: list[float] | None, page_width: float) -> bool:
    if not box:
        return False
    return 0 < (box[2] - box[0]) < 0.55 * page_width


def _is_full_width(box: list[float] | None, page_width: float) -> bool:
    if not box:
        return False
    width = page_width if page_width > 1 else 1000.0
    return (box[2] - box[0]) >= 0.55 * width


def _is_dialogue(line: str) -> bool:
    t = (line or "").strip()
    return bool(t) and t[0] in _DIALOGUE_OPEN


def _is_short_verse(text: str) -> bool:
    t = normalize_line(text)
    if not t or _YEAR_LEAD.match(text.strip()) or _looks_footnote(text):
        return False
    if text.strip() in _NOT_TITLE or _UNIT.match(text.strip()):
        return False
    if _is_dialogue(text):
        return False
    if len(t) <= 40:
        return True
    return bool(re.search(r"[，。？！；、]$", text.strip())) and len(t) <= 56


def _is_long_prose(text: str) -> bool:
    t = normalize_line(text)
    return len(t) >= 42


def heading_plain(line: str) -> str:
    match = _HEADING.match((line or "").strip())
    return match.group(2).strip() if match else ""


def is_pedagogy_head(line: str) -> bool:
    raw = normalize_line(heading_plain(line) or line)
    return raw in _NOT_TITLE or (line or "").strip() in _NOT_TITLE


def is_numbered_head(line: str) -> bool:
    body = heading_plain(line)
    if not body:
        return False
    stripped = re.sub(r"\$\^\{[^}]+\}\$", "", body).strip()
    return bool(_NUMBERED_HEAD.match(stripped) or _NUMBERED_HEAD.match(body))


def is_subtitle_head(line: str) -> bool:
    body = heading_plain(line)
    return bool(body) and bool(_SUBTITLE_HEAD.match(body))


def _following_nonempty(lines: list[str], idx: int, end: int | None = None) -> str:
    limit = end if end is not None else len(lines) - 1
    look = idx + 1
    while look <= limit and not lines[look].strip():
        look += 1
    if look > limit:
        return ""
    return lines[look]


def _is_work_title(line: str, titles: set[str], next_line: str = "") -> bool:
    match = _HEADING.match(line.strip())
    if not match:
        return False
    raw = normalize_line(match.group(2))
    if not raw or raw in _COVER or is_pedagogy_head(line):
        return False
    if _UNIT.match(raw) or _LESSON.match(raw) or is_numbered_head(line):
        return False
    if is_subtitle_head(line):
        return False
    if titles and any(raw == t or raw.startswith(t) or t.startswith(raw) for t in titles):
        return True
    if next_line and _is_author_line(next_line) and 2 <= len(raw) <= 16:
        return True
    return False


def _heading_key(line: str) -> str:
    match = _HEADING.match((line or "").strip())
    if not match:
        return ""
    return normalize_line(match.group(2))


def _is_author_line(line: str) -> bool:
    t = line.strip()
    if not t or t.startswith("#") or t.startswith("!") or t.startswith(":::"):
        return False
    if _DATE.match(t) or t == "---":
        return False
    body = normalize_line(t)
    compact = re.sub(r"\s+", "", t)
    if _BOOK_SRC.match(compact) or _BOOK_SRC.match(body):
        return True
    return bool(body and _AUTHOR.match(body))


def _is_page_leading(lines: list[str], idx: int) -> bool:
    for look in range(idx):
        raw = lines[look].strip()
        if not raw or raw == "---" or raw.startswith(":::"):
            continue
        if _IMG.match(raw) or raw.startswith("$$"):
            continue
        if _HEADING.match(raw):
            key = normalize_line(heading_plain(raw))
            if (
                not key
                or key in _COVER
                or is_pedagogy_head(raw)
                or _UNIT.match(key)
                or _LESSON.match(key)
            ):
                continue
            return False
        if _is_author_line(raw) or _is_date_line(raw) or _looks_footnote(raw):
            continue
        return False
    return True


def _main_column(blocks: list[dict[str, Any]] | None, page_width: float) -> tuple[float, float]:
    width = page_width if page_width > 1 else 1000.0
    wide: list[list[float]] = []
    for block in blocks or []:
        box = block_bbox(block)
        if box and (box[2] - box[0]) >= 0.45 * width:
            wide.append(box)
    if not wide:
        return 0.0, width
    lefts = sorted(box[0] for box in wide)
    rights = sorted(box[2] for box in wide)
    return lefts[len(lefts) // 4], rights[(3 * len(rights)) // 4]


def heading_align(
    box: list[float] | None,
    col0: float,
    col1: float,
) -> str:
    if not box or len(box) < 4:
        return "unknown"
    span = max(col1 - col0, 1.0)
    left = box[0] - col0
    right = col1 - box[2]
    rel_w = (box[2] - box[0]) / span
    if left <= 0.12 * span:
        return "left"
    if rel_w < 0.72 and left > 0.18 * span:
        return "center"
    if abs(left - right) <= 0.14 * span and rel_w < 0.72:
        return "center"
    return "other"


def heading_box(
    blocks: list[dict[str, Any]] | None,
    line: str,
) -> list[float] | None:
    need = normalize_line(heading_plain(line))
    if not need:
        return None
    for block in blocks or []:
        btype = str(block.get("type") or "").strip().lower()
        if btype in _SKIP_TYPES:
            continue
        text = normalize_line(block_text(block))
        if not text or not _text_matches(text, need):
            continue
        box = block_bbox(block)
        if box:
            return box
    return None


def classify_heading_mark(
    line: str,
    titles: set[str],
    *,
    next_line: str = "",
    page_leading: bool = False,
    align: str = "unknown",
) -> str | None:
    if not _HEADING.match((line or "").strip()):
        return None
    raw = _heading_key(line)
    if not raw:
        return None
    if raw in _COVER or is_pedagogy_head(line) or _UNIT.match(raw) or _LESSON.match(raw):
        return None
    if is_subtitle_head(line):
        return "kb-subtitle"
    if _is_work_title(line, titles, next_line):
        return "kb-title"
    if is_numbered_head(line):
        return "kb-section-center" if align == "center" else "kb-section"
    if _WRITE_LESSON.match(raw):
        return "kb-section" if align == "left" else "kb-title"
    if _BOOK_SRC.match(raw) and page_leading and align != "left":
        return "kb-title"
    if align == "center":
        return "kb-title" if page_leading else "kb-section-center"
    return "kb-section"


def _is_date_line(line: str) -> bool:
    return bool(_DATE.match((line or "").strip()))


def _looks_footnote(line: str) -> bool:
    t = line.strip()
    if not t:
        return False
    if t.startswith("〔") and "〕" in t:
        return True
    return bool(_NOTE_HEAD.match(t))


def _is_section_stop(line: str) -> bool:
    t = (line or "").strip()
    if not t:
        return False
    if t == "---":
        return True
    if t in _NOT_TITLE or _UNIT.match(t):
        return True
    match = _HEADING.match(t)
    if match and normalize_line(match.group(2)) in _NOT_TITLE:
        return True
    return False


def _verse_payload_line(line: str) -> bool:
    t = (line or "").strip()
    if not t or t.startswith(":::") or t.startswith("![") or t.startswith("$$"):
        return False
    if _is_section_stop(t) or _is_date_line(t) or _looks_footnote(t):
        return False
    if _HEADING.match(t) or _LESSON.match(t):
        return False
    return True


def _box_inside(inner: list[float], outer: list[float] | None, pad: float = 8.0) -> bool:
    if not outer or len(inner) < 4:
        return False
    return (
        inner[0] >= outer[0] - pad
        and inner[1] >= outer[1] - pad
        and inner[2] <= outer[2] + pad
        and inner[3] <= outer[3] + pad
    )


def _page_stop(lines: list[str]) -> int:
    hr = next((n for n, ln in enumerate(lines) if ln.strip() == "---"), None)
    limit = hr if hr is not None else len(lines)
    for idx in range(limit):
        if _is_section_stop(lines[idx]) and lines[idx].strip() != "---":
            return idx
    return limit


def split_page_segments(lines: list[str], titles: set[str]) -> list[dict[str, Any]]:
    stop = _page_stop(lines)
    title_idxs = [
        i
        for i in range(stop)
        if _is_work_title(lines[i], titles, _following_nonempty(lines, i, stop - 1))
    ]
    segs: list[dict[str, Any]] = []
    if not title_idxs:
        if stop > 0:
            segs.append({"start": 0, "end": stop - 1, "title": None})
        return segs
    if title_idxs[0] > 0:
        segs.append({"start": 0, "end": title_idxs[0] - 1, "title": None})
    for i, tidx in enumerate(title_idxs):
        end = title_idxs[i + 1] - 1 if i + 1 < len(title_idxs) else stop - 1
        segs.append({"start": tidx, "end": end, "title": _heading_key(lines[tidx]) or None})
    return segs


def _advance_past_head(lines: list[str], start: int, end: int) -> int:
    idx = start
    if idx <= end and _HEADING.match(lines[idx].strip()):
        idx += 1
        while idx <= end and not lines[idx].strip():
            idx += 1
        if idx <= end and is_subtitle_head(lines[idx]):
            idx += 1
            while idx <= end and not lines[idx].strip():
                idx += 1
        if idx <= end and _is_author_line(lines[idx]):
            idx += 1
    return idx


def _segment_body_lines(lines: list[str], start: int, end: int) -> list[str]:
    out: list[str] = []
    for idx in range(_advance_past_head(lines, start, end), end + 1):
        raw = lines[idx]
        t = raw.strip()
        if _is_section_stop(raw) or _is_date_line(raw) or _looks_footnote(t):
            break
        if _verse_payload_line(raw):
            out.append(raw)
    return out


def _work_body_spans(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    first = last = None

    def flush() -> None:
        nonlocal first, last
        if first is not None and last is not None:
            spans.append((first, last))
        first = last = None

    for idx in range(_advance_past_head(lines, start, end), end + 1):
        raw = lines[idx]
        t = raw.strip()
        if _is_section_stop(raw) or _is_date_line(raw) or _looks_footnote(t):
            break
        if t and _is_long_prose(raw):
            break
        if _IMG.match(t) or t.startswith("$$"):
            flush()
            continue
        if _verse_payload_line(raw):
            if first is None:
                first = idx
            last = idx
    flush()
    return spans


def _verse_head(lines: list[str]) -> list[str]:
    head: list[str] = []
    long_run = 0
    for raw in lines:
        t = raw.strip()
        if not t or not _verse_payload_line(raw):
            continue
        if _is_dialogue(t):
            if head:
                head.append(t)
            continue
        if _is_long_prose(t):
            long_run += 1
            if long_run >= 2:
                break
            continue
        long_run = 0
        if _is_short_verse(t):
            head.append(t)
    return head


def classify_work(
    lines: list[str],
    boxes: list[list[float]] | None = None,
    page_width: float = 1000.0,
) -> str | None:
    payload = [ln.strip() for ln in lines if ln.strip() and _verse_payload_line(ln)]
    if len(payload) < 2:
        return None
    width = page_width if page_width > 1 else 1000.0
    head = _verse_head(payload)
    head_ev = [ln for ln in head if not _is_dialogue(ln)]
    if len(head_ev) >= 2:
        usable = [box for box in (boxes or []) if _is_narrow(box, width)]
        if len(usable) < 2:
            usable = [box for box in (boxes or []) if not _is_full_width(box, width)]
        if len(usable) >= 2:
            return classify_line_boxes(usable, width)
        return "kb-verse"
    quoted = [ln for ln in payload if _is_dialogue(ln)]
    evidence = [ln for ln in payload if not _is_dialogue(ln)]
    long_n = sum(1 for ln in evidence if _is_long_prose(ln))
    short_n = sum(1 for ln in evidence if _is_short_verse(ln))
    wide_n = sum(1 for box in (boxes or []) if _is_full_width(box, width))
    if wide_n >= 2:
        return None
    if long_n >= 2 and long_n >= short_n:
        return None
    if len(quoted) >= 3 and long_n >= 1:
        return None
    if short_n < 2:
        return None
    if long_n and short_n < max(2, (len(evidence) + 1) // 2):
        return None
    narrow = [box for box in (boxes or []) if _is_narrow(box, width)]
    if len(narrow) >= 2:
        return classify_line_boxes(narrow, width)
    if len(boxes or []) >= 2:
        return classify_line_boxes(list(boxes or []), width)
    return "kb-verse"


def _text_matches(got: str, need: str) -> bool:
    if not got or not need:
        return False
    return got == need or need in got or got in need


def boxes_for_lines(
    blocks: list[dict[str, Any]] | None,
    lines: list[str],
    page_width: float = 1000.0,
    layout_lines: list[tuple[str, list[float]]] | None = None,
) -> list[list[float]]:
    want = [normalize_line(ln) for ln in lines if normalize_line(ln)]
    if not want:
        return []
    boxes: list[list[float]] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        btype = str(block.get("type") or "").strip().lower()
        if btype in _SKIP_TYPES or btype in {"title", "heading", "page_title"}:
            continue
        text = block_text(block)
        norm = normalize_line(text)
        if not norm or not any(_text_matches(norm, item) for item in want):
            continue
        box = block_bbox(block)
        if box:
            boxes.append(box)
    return boxes


def apply_chinese_layout(
    source: str,
    *,
    blocks: list[dict[str, Any]] | None = None,
    titles: set[str] | None = None,
    layout_lines: list[tuple[str, list[float]]] | None = None,
    page_width: float = 1000.0,
    work_kind: str | None = None,
    work_kinds: dict[str, str | None] | None = None,
    current_work: str | None = None,
) -> str:
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    work_titles = titles or set()
    marks: dict[int, str] = {}
    span_kind: dict[tuple[int, int], str] = {}
    hr = next((n for n, ln in enumerate(lines) if ln.strip() == "---"), None)

    col0, col1 = _main_column(blocks, page_width)
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if _IMG.match(stripped) or stripped.startswith("$$"):
            marks[i] = "kb-center"
            i += 1
            continue
        if _HEADING.match(stripped):
            nxt = _following_nonempty(lines, i)
            mark = classify_heading_mark(
                stripped,
                work_titles,
                next_line=nxt,
                page_leading=_is_page_leading(lines, i),
                align=heading_align(heading_box(blocks, stripped), col0, col1),
            )
            if mark:
                marks[i] = mark
                if mark in {"kb-title", "kb-section-center"}:
                    look = i + 1
                    while look < len(lines) and not lines[look].strip():
                        look += 1
                    if look < len(lines) and is_subtitle_head(lines[look]):
                        look += 1
                        while look < len(lines) and not lines[look].strip():
                            look += 1
                    if look < len(lines) and _is_author_line(lines[look]):
                        marks[look] = "kb-author"
            i += 1
            continue
        i += 1

    current = current_work
    for seg in split_page_segments(lines, work_titles):
        if seg.get("title"):
            current = str(seg["title"])
        kind: str | None = None
        if work_kinds is not None:
            kind = work_kinds.get(current or "")
        elif work_kind in {"kb-verse", "kb-verse-center"} and not seg.get("title"):
            kind = work_kind
        else:
            body = _segment_body_lines(lines, int(seg["start"]), int(seg["end"]))
            kind = classify_work(
                body,
                boxes_for_lines(blocks, body, page_width, layout_lines),
                page_width,
            )
        if kind not in {"kb-verse", "kb-verse-center"}:
            continue
        chunk = "\n".join(lines[int(seg["start"]) : int(seg["end"]) + 1])
        if "普通高中教科书" in chunk:
            continue
        if any(
            normalize_line(heading_plain(ln)) in _COVER
            for ln in lines[int(seg["start"]) : int(seg["end"]) + 1]
        ):
            continue
        for span in _work_body_spans(lines, int(seg["start"]), int(seg["end"])):
            span_kind[span] = kind

    owned: set[int] = set()
    for start, end in span_kind:
        owned.update(range(start, end + 1))

    idx = 0
    while idx < len(lines):
        if idx in owned or (hr is not None and idx > hr) or not _is_date_line(lines[idx]):
            idx += 1
            continue
        start = idx
        last = idx
        look = idx + 1
        while look < len(lines) and (hr is None or look < hr):
            if not lines[look].strip():
                look += 1
                continue
            if _is_date_line(lines[look]):
                last = look
                look += 1
                continue
            break
        span_kind[(start, last)] = "kb-date"
        owned.update(range(start, last + 1))
        idx = last + 1

    out: list[str] = []
    idx = 0
    while idx < len(lines):
        span = next(((a, b) for a, b in span_kind if a == idx), None)
        if span:
            a, b = span
            body = "\n".join(lines[a : b + 1]).rstrip()
            out.append(f"::: {span_kind[span]}\n{body}\n:::")
            idx = b + 1
            continue
        if idx in marks and idx not in owned:
            out.append(f"::: {marks[idx]}\n{lines[idx].rstrip()}\n:::")
            idx += 1
            continue
        out.append(lines[idx])
        idx += 1
    result = "\n".join(out)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def load_mineru_pages(book: Path) -> dict[int, list[dict[str, Any]]]:
    mineru = book / "mineru"
    if not mineru.is_dir():
        return {}
    v2 = next(iter(sorted(mineru.glob("*_content_list_v2.json"))), None)
    if v2 and v2.is_file():
        try:
            data = json.loads(v2.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, list) and data and isinstance(data[0], list):
            return {i + 1: [b for b in page if isinstance(b, dict)] for i, page in enumerate(data)}
    blocks_path = mineru / "blocks.json"
    if not blocks_path.is_file():
        return {}
    try:
        rows = json.loads(blocks_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    grouped: dict[int, list[dict[str, Any]]] = {}
    if isinstance(rows, list):
        for block in rows:
            if not isinstance(block, dict):
                continue
            try:
                page_no = int(block.get("page_idx")) + 1
            except (TypeError, ValueError):
                page_no = 1
            grouped.setdefault(page_no, []).append(block)
    return grouped


def load_layout_pages(book: Path) -> list[Any]:
    path = book / "mineru" / "layout.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    pages = data.get("pdf_info") if isinstance(data, dict) else None
    return pages if isinstance(pages, list) else []


def load_layout_lines(book: Path, page_no: int, pages: list[Any] | None = None) -> list[tuple[str, list[float]]]:
    pages = pages if pages is not None else load_layout_pages(book)
    if not pages or page_no < 1 or page_no > len(pages):
        return []
    page = pages[page_no - 1]
    if not isinstance(page, dict):
        return []
    out: list[tuple[str, list[float]]] = []
    stack: list[Any] = list(page.get("preproc_blocks") or page.get("para_blocks") or [])
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        stack.extend(item.get("blocks") or [])
        for line in item.get("lines") or []:
            if not isinstance(line, dict):
                continue
            box = line.get("bbox")
            if not isinstance(box, (list, tuple)) or len(box) < 4:
                continue
            parts: list[str] = []
            for span in line.get("spans") or []:
                if isinstance(span, dict) and span.get("type") in (None, "text", "equation_inline"):
                    parts.append(str(span.get("content") or ""))
            try:
                nums = [float(box[0]), float(box[1]), float(box[2]), float(box[3])]
            except (TypeError, ValueError):
                continue
            out.append(("".join(parts), nums))
    return out


def _plain_body(text: str) -> str:
    return re.sub(r"\n{2,}", "\n", (text or "").strip())


def page_width_of(blocks: list[dict[str, Any]]) -> float:
    right = 0.0
    for block in blocks:
        box = block_bbox(block)
        if box:
            right = max(right, box[2])
    return right if right >= 200 else 1000.0


def tag_chinese_layout(book_id: str) -> dict[str, Any]:
    """语文教材：先按篇切开，一篇一种正文格式。只写中间文件。"""
    sid = (book_id or "").strip()
    if not _SAFE_ID.match(sid):
        raise ValueError("book_id required")
    from app import library
    from app.mid_text import strip_skin

    meta = library.read_meta(sid)
    if not is_chinese_textbook(meta):
        return {
            "book_id": sid,
            "skipped": True,
            "reason": "not chinese textbook",
            "subject": str((meta or {}).get("subject") or ""),
            "category": str((meta or {}).get("category") or ""),
        }
    book = library.book_dir(sid)
    pages_dir = book / "pages"
    if not pages_dir.is_dir():
        raise FileNotFoundError(str(pages_dir))
    toc = library.read_toc(sid)
    titles = toc_work_titles(toc if isinstance(toc, dict) else {})
    mineru = load_mineru_pages(book)
    layout_pages = load_layout_pages(book)
    collected: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    incoming: str | None = None
    skipped_toc = 0
    pages = 0
    for path in sorted(pages_dir.glob("*.md")):
        try:
            page_no = int(path.stem)
        except ValueError:
            continue
        pages += 1
        source = path.read_text(encoding="utf-8")
        existing = library.get_page_mid(sid, page_no)
        if existing and "::: kb-page-toc" in existing:
            skipped_toc += 1
            incoming = None
            continue
        lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        blocks = mineru.get(page_no, [])
        width = page_width_of(blocks)
        layout = load_layout_lines(book, page_no, layout_pages) if blocks else None
        page_incoming = incoming
        for seg in split_page_segments(lines, titles):
            if seg.get("title"):
                incoming = str(seg["title"])
            work = incoming or f"_page_{page_no}"
            if incoming is None:
                incoming = work
            body = _segment_body_lines(lines, int(seg["start"]), int(seg["end"]))
            slot = collected.setdefault(work, {"lines": [], "boxes": [], "width": width})
            slot["lines"].extend(body)
            slot["boxes"].extend(boxes_for_lines(blocks, body, width, layout))
            slot["width"] = max(float(slot["width"]), width)
        rows.append(
            {
                "page_no": page_no,
                "source": source,
                "existing": existing,
                "blocks": blocks,
                "layout": layout,
                "width": width,
                "incoming": page_incoming,
            }
        )
    work_kinds = {
        key: classify_work(list(slot["lines"]), list(slot["boxes"]), float(slot["width"]))
        for key, slot in collected.items()
    }
    changed = 0
    for row in rows:
        tagged = apply_chinese_layout(
            str(row["source"]),
            blocks=list(row["blocks"]),
            titles=titles,
            layout_lines=row["layout"],
            page_width=float(row["width"]),
            work_kinds=work_kinds,
            current_work=row["incoming"],
        )
        if _plain_body(strip_skin(tagged)) != _plain_body(str(row["source"])):
            continue
        if tagged != (row["existing"] or ""):
            library.write_mid(sid, int(row["page_no"]), tagged)
            changed += 1
    return {
        "book_id": sid,
        "skipped": False,
        "pages": pages,
        "changed": changed,
        "skipped_toc": skipped_toc,
        "titles": len(titles),
        "works": len(work_kinds),
    }
