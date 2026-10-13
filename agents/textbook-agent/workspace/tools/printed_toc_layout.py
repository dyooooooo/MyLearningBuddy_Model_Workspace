"""Printed catalog layout from page facts. Pure functions: no model, no disk.

lines -> rows, per column of each catalog page:
  pieces at the same height are joined, page numbers attached, wrapped lines
  and split unit titles merged. A page number the text layer lacks becomes a
  hole for the model to read from the image.
rows  -> levels, for the whole book at once:
  print size, weight and text indent are compared on a stack, so one print
  style always lands on one level, on every page.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from typing import Any

EDGE_BAND = 0.06
SIZE_TOL = 0.5
INDENT_TOL = 4.0
INDENT_EM = 0.6
SPACE_GAP = 0.3
BLANK_MIN = 0.15
WRAP_PITCH = 1.35
ROW_OVERLAP = 0.5
MIN_GUTTER = 6.0
PREFIX_SCALE = 1.3

_CN_NUM = "一二三四五六七八九十百零〇"
_BOLD_FONT = re.compile(r"bold|semibold|heavy|black|demi", re.I)
_TOC_WORD = re.compile(r"^(目录|目|录|contents)$", re.I)
_GLYPH_LATIN = str.maketrans({"\u7283": "A", "\u7285": "B"})
_NOTE = re.compile(r"^注[:：]")
_PAGE_ONLY = re.compile(
    r"^(?:/\s*)?(?:\(\s*p\.?\s*(?P<p1>[\d ]*)\)|(?P<p2>\d(?:\s?\d){0,3}))$", re.I
)
_PAGE_TAIL = re.compile(
    r"^(?P<title>.*?\S)(?:\s+|\s*/\s*|\s*(?:\.{2,}|…+|[·•]{2,})\s*)"
    r"(?:\(\s*p\.?\s*(?P<p1>[\d ]*)\)|(?P<p2>\d(?:\s?\d){0,3}))\s*$",
    re.I,
)
_LEADER_TAIL = re.compile(r"^(?P<title>.*?\S)\s*(?:\.{3,}|…{2,}|[·•]{3,})\s*$")
_BARE_UNIT = re.compile(rf"^第\s*[\d{_CN_NUM}]+\s*(单元|章|部分|编)$")
_STARTS_ITEM = re.compile(rf"^(\d|第|[{_CN_NUM}]+[、\s]|[（(]|\*|[A-H]\s)")
_LABEL_PREFIX = re.compile(r"^[A-Z][A-Za-z -]{1,24}:\s")
_SPACED_NUMBER = re.compile(rf"第\s*([\d{_CN_NUM}]{{1,4}})\s*(章|节|单元|课|部分|编)")
_LEAD_MARKS = set("0123456789.*")


def tidy(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").translate(_GLYPH_LATIN)
    text = re.sub(r"\s+", " ", text).strip()
    return _SPACED_NUMBER.sub(r"第\1\2", text)


def _digits(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = re.sub(r"\s", "", raw)
    if not value.isdigit():
        return None
    page = int(value)
    return page if 1 <= page <= 999 else None


# ---------------------------------------------------------------- page lines


def header_key(text: str) -> str:
    return re.sub(r"[\d\s]", "", text)


def page_tail_count(lines: list[dict[str, Any]]) -> int:
    """How many lines end with a printed page number."""
    return sum(1 for ln in lines if _PAGE_TAIL.match(str(ln.get("text") or "")))


def text_layer_lines(page: Any, edge: float = EDGE_BAND) -> list[dict[str, Any]]:
    """One PyMuPDF page -> horizontal text lines with chars, without page edges."""
    height = page.rect.height
    out: list[dict[str, Any]] = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            if abs(line["dir"][1]) > 0.1:
                continue
            chars: list[dict[str, Any]] = []
            for span in line["spans"]:
                bold = bool(span["flags"] & 16) or bool(_BOLD_FONT.search(span["font"]))
                for ch in span["chars"]:
                    x0, y0, x1, y1 = ch["bbox"]
                    chars.append({
                        "c": unicodedata.normalize("NFKC", ch["c"]),
                        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                        "size": span["size"], "bold": bold, "color": span["color"],
                    })
            ink = [c for c in chars if c["c"].strip()]
            if not ink:
                continue
            y0 = min(c["y0"] for c in ink)
            y1 = max(c["y1"] for c in ink)
            middle = (y0 + y1) / 2
            if middle < height * edge or middle > height * (1 - edge):
                continue
            out.append(_line(chars, ink, y0, y1))
    return out


def _line(chars: list[dict[str, Any]], ink: list[dict[str, Any]], y0: float, y1: float) -> dict:
    return {
        "chars": chars,
        "text": _chars_text(chars),
        "x0": min(c["x0"] for c in ink),
        "x1": max(c["x1"] for c in ink),
        "y0": y0,
        "y1": y1,
    }


def model_lines(rows: list[dict[str, Any]], width: float, height: float) -> list[dict[str, Any]]:
    """Model rows with 0-1000 boxes -> lines in page points (no chars)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        box = row.get("bbox")
        text = tidy(str(row.get("text") or ""))
        if not text or not isinstance(box, list) or len(box) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        x0, x1 = sorted((x0 * width / 1000, x1 * width / 1000))
        y0, y1 = sorted((y0 * height / 1000, y1 * height / 1000))
        middle = (y0 + y1) / 2
        if y1 - y0 < 1 or middle < height * EDGE_BAND or middle > height * (1 - EDGE_BAND):
            continue
        given = row.get("page")
        out.append({
            "chars": None, "text": text, "x0": x0, "x1": x1, "y0": y0, "y1": y1,
            "size": round((y1 - y0) * 0.8, 1), "bold": False, "color": row.get("color", 0),
            "given_page": _digits(str(given)) if given is not None and given != "" else None,
        })
    return out


def _unreadable(ch: str) -> bool:
    """Glyphs mapped to the private use area (some books' digits): no text."""
    return any(unicodedata.category(c) == "Co" for c in ch)


def _chars_text(chars: list[dict[str, Any]]) -> str:
    ink = [c for c in chars if c["c"].strip()]
    height = statistics.median(c["y1"] - c["y0"] for c in ink) if ink else 1.0
    kept: list[dict[str, Any]] = []
    for ch in chars:
        if ch["c"].strip():
            if not _unreadable(ch["c"]):
                kept.append(ch)
            continue
        wide = ch["x1"] - ch["x0"] >= BLANK_MIN * height
        covered = any(c["x0"] < (ch["x0"] + ch["x1"]) / 2 < c["x1"] for c in ink)
        if wide and not covered:
            kept.append(dict(ch, c=" "))
    parts: list[str] = []
    prev = None
    for ch in sorted(kept, key=lambda c: c["x0"]):
        if prev is not None and prev["c"] != " " and ch["c"] != " ":
            if ch["x0"] - prev["x1"] > SPACE_GAP * height:
                parts.append(" ")
        parts.append(ch["c"])
        prev = ch
    return tidy("".join(parts))


# ---------------------------------------------------------------- one page


def page_rows(
    lines: list[dict[str, Any]], pdf_page: int, model_wraps: bool = True
) -> list[dict[str, Any]]:
    """Lines of one catalog page -> rows in reading order (column, then top to bottom).

    model_wraps=False leaves rows without real letter sizes unjoined; their sizes
    are guessed from box height, so the vision model judges those wraps instead.
    """
    lines = [ln for ln in lines if ln["text"]]
    for ln in lines:
        ln["token"] = _PAGE_ONLY.match(ln["text"])
    for ln in lines:
        if ln["token"] and any(_inside(ln, other) for other in lines if not other["token"]):
            ln["token"] = None
    cuts = _column_cuts(lines)
    rows: list[dict[str, Any]] = []
    for col in range(len(cuts) + 1):
        lo = cuts[col - 1] if col else float("-inf")
        hi = cuts[col] if col < len(cuts) else float("inf")
        mine = [ln for ln in lines if lo <= ln["x0"] < hi]
        rows.extend(_frame_rows(mine, pdf_page, col, model_wraps))
    return rows


def _paged(lines: list[dict[str, Any]]) -> int:
    return sum(1 for ln in lines if ln["token"] or _PAGE_TAIL.match(ln["text"]))


def _column_cuts(lines: list[dict[str, Any]]) -> list[float]:
    covered: list[list[float]] = []
    for ln in sorted(lines, key=lambda item: item["x0"]):
        if covered and ln["x0"] <= covered[-1][1] + 0.5:
            covered[-1][1] = max(covered[-1][1], ln["x1"])
        else:
            covered.append([ln["x0"], ln["x1"]])
    cuts: list[float] = []
    for (_, end), (start, _) in zip(covered, covered[1:]):
        if start - end < MIN_GUTTER:
            continue
        cut = (end + start) / 2
        left = [ln for ln in lines if ln["x1"] <= cut]
        right = [ln for ln in lines if ln["x0"] >= cut]
        titled = all(sum(1 for ln in side if not ln["token"]) >= 2 for side in (left, right))
        if titled and _paged(left) >= 2 and _paged(right) >= 2:
            cuts.append(cut)
    return cuts


def _inside(token: dict[str, Any], line: dict[str, Any]) -> bool:
    """A number drawn inside a title's span (a chapter badge digit) belongs to it."""
    within = token["x0"] >= line["x0"] - 1 and token["x1"] <= line["x1"] + 1
    return within and _overlap(token, line) >= ROW_OVERLAP


def _overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    shared = min(a["y1"], b["y1"]) - max(a["y0"], b["y0"])
    least = min(a["y1"] - a["y0"], b["y1"] - b["y0"]) or 1.0
    return shared / least


def _frame_rows(
    lines: list[dict[str, Any]], pdf_page: int, col: int, model_wraps: bool = True
) -> list[dict[str, Any]]:
    titles = sorted((ln for ln in lines if not ln["token"]), key=lambda ln: ln["y0"])
    groups: list[list[dict[str, Any]]] = []
    for ln in titles:
        for group in reversed(groups[-3:]):
            if any(_overlap(ln, other) >= ROW_OVERLAP for other in group):
                group.append(ln)
                break
        else:
            groups.append([ln])
    rows = [_row(group, pdf_page, col) for group in groups]
    for row in rows:
        _inline_page(row)
        if row["print_page"] is None and row.get("given_page") is not None:
            row["print_page"] = row["given_page"]
    for ln in lines:
        if ln["token"]:
            _attach_token(rows, ln)
    rows = _merge_wrapped(rows, model_wraps)
    rows = _merge_split_units(rows)
    return [row for row in rows if _keep(row)]


def _row(group: list[dict[str, Any]], pdf_page: int, col: int) -> dict[str, Any]:
    real_size = all(ln["chars"] is not None for ln in group)
    if real_size:
        chars = [c for ln in group for c in ln["chars"]]
        text = _chars_text(chars)
        ink = sorted((c for c in chars if c["c"].strip()), key=lambda c: c["x0"])
        letters = [c for c in ink if unicodedata.category(c["c"][0]).startswith("L")]
        style = (letters or ink)[-1]
        lead = next((c for c in ink if c["c"] not in _LEAD_MARKS), ink[0])
        size, bold, color, x_text = style["size"], style["bold"], style["color"], lead["x0"]
    else:
        chars = None
        ordered = sorted(group, key=lambda ln: ln["x0"])
        text = tidy(" ".join(ln["text"] for ln in ordered))
        first = ordered[0]
        size, bold, color, x_text = first["size"], first["bold"], first["color"], first["x0"]
    return {
        "pdf_page": pdf_page,
        "col": col,
        "text": text,
        "chars": chars,
        "x0": min(ln["x0"] for ln in group),
        "x1": max(ln["x1"] for ln in group),
        "y0": min(ln["y0"] for ln in group),
        "y1": max(ln["y1"] for ln in group),
        "x_text": x_text,
        "size": round(float(size), 1),
        "bold": bool(bold),
        "real_size": real_size,
        "color": color,
        "print_page": None,
        "given_page": next((ln.get("given_page") for ln in group if ln.get("given_page") is not None), None),
        "hole": None,
        "hole_box": None,
    }


def _inline_page(row: dict[str, Any]) -> None:
    found = _PAGE_TAIL.match(row["text"])
    if found:
        row["text"] = found.group("title").strip()
        if found.group("p2") is not None:
            row["print_page"] = _digits(found.group("p2"))
            return
        page = _digits(found.group("p1"))
        if page is not None:
            row["print_page"] = page
            return
        row["hole"] = "inline"
        row["hole_box"] = _tail_box(row, "(")
        return
    leader = _LEADER_TAIL.match(row["text"])
    if leader:
        row["text"] = leader.group("title").strip()
        row["hole"] = "leader"
        h = row["y1"] - row["y0"]
        row["hole_box"] = [row["x1"] - h * 0.5, row["y0"] - 2, row["x1"] + h * 3.5, row["y1"] + 2]


def _tail_box(row: dict[str, Any], opener: str) -> list[float]:
    ink = sorted((c for c in row["chars"] or [] if c["c"].strip()), key=lambda c: c["x0"])
    starts = [i for i, c in enumerate(ink) if c["c"] == opener]
    tail = ink[starts[-1]:] if starts else ink[-3:]
    if not tail:
        return [row["x1"] - 30, row["y0"], row["x1"] + 2, row["y1"]]
    return [tail[0]["x0"] - 1, row["y0"] - 1, tail[-1]["x1"] + 1, row["y1"] + 1]


def _attach_token(rows: list[dict[str, Any]], token: dict[str, Any]) -> None:
    found = token["token"]
    page = _digits(found.group("p2")) if found.group("p2") is not None else _digits(found.group("p1"))
    height = token["y1"] - token["y0"]
    numbered = [
        r for r in rows
        if _overlap(r, token) >= ROW_OVERLAP and token["x1"] <= r["x0"] + 2
        and height > PREFIX_SCALE * (r["y1"] - r["y0"])
    ]
    if numbered:
        target = min(numbered, key=lambda r: r["x0"])
        target["text"] = f'{token["text"]} {target["text"]}'
        target["x0"] = token["x0"]
        return
    free = [r for r in rows if r["print_page"] is None and r["hole"] is None]
    beside = [r for r in free if _overlap(r, token) >= ROW_OVERLAP and r["x1"] <= token["x0"] + 2]
    if beside:
        target = max(beside, key=lambda r: r["x1"])
    else:
        above = [r for r in free if r["y0"] < token["y0"] and token["y0"] - r["y1"] < 1.5 * height]
        if not above:
            return
        target = max(above, key=lambda r: r["y0"])
    if page is not None:
        target["print_page"] = page
    else:
        target["hole"] = "token"
        target["hole_box"] = [token["x0"] - 1, token["y0"] - 1, token["x1"] + 1, token["y1"] + 1]


def _join(a: str, b: str) -> str:
    if b.startswith("——") or (a and b and _is_cjk(a[-1]) and _is_cjk(b[0])):
        return a + b
    return a + " " + b


def _is_cjk(ch: str) -> bool:
    return "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def _absorb(row: dict[str, Any], nxt: dict[str, Any], sep_text: str) -> dict[str, Any]:
    merged = dict(row)
    merged["text"] = sep_text
    merged["chars"] = None
    merged["x1"] = max(row["x1"], nxt["x1"])
    merged["y1"] = max(row["y1"], nxt["y1"])
    merged["print_page"] = nxt["print_page"]
    merged["hole"] = nxt["hole"]
    merged["hole_box"] = nxt["hole_box"]
    merged["merged"] = row.get("merged", 1) + nxt.get("merged", 1)
    merged["real_size"] = bool(row.get("real_size", True) and nxt.get("real_size", True))
    return merged


def _merge_wrapped(rows: list[dict[str, Any]], model_wraps: bool = True) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        prev = out[-1] if out else None
        measured = model_wraps or (prev is not None and prev.get("real_size") and row.get("real_size"))
        if prev is not None and measured and _wraps(prev, row):
            out[-1] = _absorb(prev, row, _join(prev["text"], row["text"]))
        else:
            out.append(row)
    return out


def _wraps(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
    if prev["print_page"] is not None or prev["hole"] is not None:
        return False
    if nxt["text"].startswith("——"):
        return True
    if _STARTS_ITEM.match(nxt["text"]) or _LABEL_PREFIX.match(nxt["text"]):
        return False
    if abs(prev["size"] - nxt["size"]) > SIZE_TOL or prev["bold"] != nxt["bold"]:
        return False
    if nxt["x0"] < prev["x0"] - 2:
        return False
    height = prev["y1"] - prev["y0"] or 1.0
    return nxt["y0"] - prev["y0"] < WRAP_PITCH * height


def _merge_split_units(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(rows):
        row = rows[i]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if (
            nxt is not None
            and row["print_page"] is None
            and row["hole"] is None
            and _BARE_UNIT.match(row["text"])
            and (nxt["print_page"] is not None or nxt["hole"] is not None)
            and not _STARTS_ITEM.match(nxt["text"])
            and "/" not in nxt["text"]
        ):
            out.append(_absorb(row, nxt, row["text"] + " " + nxt["text"]))
            i += 2
            continue
        out.append(row)
        i += 1
    return out


def _keep(row: dict[str, Any]) -> bool:
    text = row["text"]
    plain = re.sub(r"[\s|│丨]", "", text)
    if not plain or _TOC_WORD.match(plain):
        return False
    if _NOTE.match(plain) and row["print_page"] is None:
        return False
    return any(unicodedata.category(ch).startswith("L") for ch in plain)


# ---------------------------------------------------------------- levels


def _key(row: dict[str, Any]) -> tuple[float, bool]:
    return (round(row["size"] * 2) / 2, row["bold"])


def _frame_offsets(rows: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    """Shift of each (page, column) against the first, matched on shared print styles."""
    frames: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        frames.setdefault((row["pdf_page"], row["col"]), []).append(row)
    ref: dict[tuple[float, bool], float] = {}
    ref_min: float | None = None
    offsets: dict[tuple[int, int], float] = {}
    for frame, items in frames.items():
        mins: dict[tuple[float, bool], float] = {}
        for row in items:
            key = _key(row)
            mins[key] = min(mins.get(key, row["x_text"]), row["x_text"])
        shared = [mins[k] - ref[k] for k in mins if k in ref]
        low = min(mins.values())
        if shared:
            offset = statistics.median(shared)
        elif ref_min is not None:
            offset = low - ref_min
        else:
            offset = 0.0
        offsets[frame] = offset
        for key, x in mins.items():
            ref.setdefault(key, x - offset)
        ref_min = low - offset if ref_min is None else min(ref_min, low - offset)
    return offsets


def _stronger(a: tuple[float, bool, float], b: tuple[float, bool, float]) -> bool:
    """Print style a outranks b: bigger, else bold, else less indented."""
    if a[0] > b[0] + SIZE_TOL:
        return True
    if b[0] > a[0] + SIZE_TOL:
        return False
    if a[1] != b[1]:
        return a[1]
    return a[2] < b[2] - max(INDENT_TOL, INDENT_EM * min(a[0], b[0]))


_TOP_TITLE = re.compile(r"^(附录|Appendices)$")


def _lift_top_titles(rows: list[dict[str, Any]]) -> None:
    """附录 beside 第N部分 shares that level. Its indented children move up with it."""
    for index, row in enumerate(rows):
        if not _TOP_TITLE.match(str(row.get("text") or "")):
            continue
        indent = float(row.get("indent") or 0)
        peer = any(
            other.get("level") == 0 and abs(float(other.get("indent") or 0) - indent) <= INDENT_TOL
            for other in rows
        )
        old = int(row["level"])
        if not peer or old == 0:
            continue
        row["level"] = 0
        for nxt in rows[index + 1:]:
            nxt_indent = float(nxt.get("indent") or 0)
            if int(nxt["level"]) <= old and nxt_indent <= indent + INDENT_TOL:
                break
            nxt["level"] = max(0, int(nxt["level"]) - old)


def assign_levels(rows: list[dict[str, Any]]) -> None:
    offsets = _frame_offsets(rows)
    stack: list[tuple[float, bool, float]] = []
    for row in rows:
        indent = row["x_text"] - offsets[(row["pdf_page"], row["col"])]
        rank = (row["size"], row["bold"], indent)
        while stack and not _stronger(stack[-1], rank):
            stack.pop()
        row["level"] = len(stack)
        row["indent"] = round(indent, 1)
        stack.append(rank)
    _lift_top_titles(rows)
