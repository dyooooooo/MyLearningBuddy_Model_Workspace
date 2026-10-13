"""Printed catalog, arch v2: the model decides each line, Python checks facts.

One page at a time:

1. decide_page   one vision call under a JSON schema. Every line gets continues,
                 level, page_printed and an optional split.
2. check_answers facts every printed catalog obeys. A line under a row that already
                 has its page number starts a new entry; a title does not wrap into
                 another column; a split keeps the letters; every line is answered once.
3. arbitrate_join a wrap the page answer wants is shown again as a zoomed crop of the
                 two lines. Agreement joins. Disagreement, or a second line that opens
                 like a new item, goes to three sampled votes.
4. build_entries joins and splits rows as decided.

Whole catalog:

5. settle_level  text-layer entries take their print-style level; a differing page
                 answer only flags ``needs_review``. Scanned rows take the page answer.
                 A level never goes more than one deeper than the entry above.
6. mark_printed_holes  a row the image shows a page number for, but none was read,
                 becomes a hole for ``fill``.

Every entry keeps ``decision``: what each step said and which one won.
"""
from __future__ import annotations

import io
import json
import re
import urllib.error
from pathlib import Path
from typing import Any

import printed_toc_layout as layout
import printed_toc_tools as tools

DECIDE_TOKENS = 4000
PAIR_TOKENS = 30
VOTES = 3
TAIL = 3
CROP_PAD = 10
CROP_RIGHT = 60
CROP_MIN_WIDTH = 400
HOLE_WIDTH = 8

DECIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "continues": {"type": "boolean"},
                    "level": {"type": "integer", "minimum": 0, "maximum": 3},
                    "page_printed": {"type": "boolean"},
                    "split": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["n", "continues", "level", "page_printed"],
            },
        }
    },
    "required": ["lines"],
}

PAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"continues": {"type": "boolean"}},
    "required": ["continues"],
}

_VISION_ERRORS = (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError)


def _text_layer(row: dict[str, Any]) -> bool:
    return row.get("fact_method") == "text_layer"


def _plain(text: str) -> str:
    return re.sub(r"\s+", "", text)


def starts_item(text: str) -> bool:
    """The line opens with an item number or a label such as ``Reading:``."""
    return bool(layout._STARTS_ITEM.match(text) or layout._LABEL_PREFIX.match(text))


# ---------------------------------------------------------------- 1 decide


def page_lines(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for n, row in enumerate(rows, 1):
        x = row.get("indent") if row.get("indent") is not None else row["x0"]
        out.append({
            "n": n,
            "text": row["text"],
            "page": row.get("print_page"),
            "x": round(float(x)),
            "style_level": row.get("level") if _text_layer(row) else None,
        })
    return out


def decide_page(
    book: Path,
    rows: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    sample: bool = False,
) -> tuple[dict[int, dict[str, Any]] | None, float]:
    """None when the page could not be read; otherwise answers keyed by line number."""
    raw = tools.page_image(book, int(rows[0]["pdf_page"]))
    if raw is None:
        return None, 0.0
    content = [
        tools._image_part(raw),
        {"type": "text", "text": tools._prompt("printed_toc_decide.md")},
        {"type": "text", "text": "上一页结尾：" + json.dumps(tail, ensure_ascii=False)},
        {"type": "text", "text": "编号行：\n" + json.dumps(page_lines(rows), ensure_ascii=False)},
    ]
    try:
        data, seconds = tools._chat(content, DECIDE_TOKENS, schema=DECIDE_SCHEMA, sample=sample)
    except _VISION_ERRORS:
        return None, 0.0
    answers: dict[int, dict[str, Any]] = {}
    for item in data.get("lines") or []:
        if isinstance(item, dict) and isinstance(item.get("n"), int):
            answers.setdefault(item["n"], item)
    return answers, seconds


# ---------------------------------------------------------------- 2 check


def check_answers(
    rows: list[dict[str, Any]], answers: dict[int, dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Page answers -> one plan per row. Facts override the answer and are written down."""
    plans: list[dict[str, Any]] = []
    for n, row in enumerate(rows, 1):
        facts: list[str] = []
        item = (answers or {}).get(n)
        if item is None:
            facts.append("unanswered" if answers is not None else "vision_failed")
            item = {}
        prev = rows[n - 2] if n > 1 else None
        wants = bool(item.get("continues"))
        joinable = wants
        if wants and prev is None:
            joinable = False
            facts.append("first_line")
        elif wants and (prev.get("print_page") is not None or prev.get("hole")):
            joinable = False
            facts.append("prev_has_page")
        elif wants and prev.get("col") != row.get("col"):
            joinable = False
            facts.append("other_column")
        parts = [str(p).strip() for p in item.get("split") or [] if str(p).strip()]
        if len(parts) >= 2 and _plain("".join(parts)) != _plain(row["text"]):
            facts.append("split_changed_text")
            parts = []
        level = item.get("level")
        plans.append({
            "continues": joinable,
            "level": int(level) if isinstance(level, int) else None,
            "page_printed": bool(item.get("page_printed", row.get("print_page") is not None)),
            "split": parts if len(parts) >= 2 else [],
            "facts": facts,
        })
    return plans


# ---------------------------------------------------------------- 3 re-ask


def pair_crop(
    book: Path,
    rows: list[dict[str, Any]],
    a: dict[str, Any],
    b: dict[str, Any],
    scale: tuple[float, float],
) -> bytes | None:
    """The two lines across the whole column, in page-image pixels."""
    from PIL import Image

    raw, _ = tools.page_raster(book, int(a["pdf_page"]))
    if raw is None:
        return None
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    sx, sy = scale if _text_layer(a) else (1.0, 1.0)
    column = [r for r in rows if r.get("col") == a.get("col")] or [a, b]
    x0 = min(float(r["x0"]) for r in column) * sx - CROP_PAD
    x1 = max(float(r["x1"]) for r in column) * sx + CROP_RIGHT
    y0 = float(a["y0"]) * sy - CROP_PAD
    y1 = float(b["y1"]) * sy + CROP_PAD
    box = (max(0, int(x0)), max(0, int(y0)), min(img.width, int(x1)), min(img.height, int(y1)))
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    crop = img.crop(box)
    if crop.width < CROP_MIN_WIDTH:
        crop = crop.resize((CROP_MIN_WIDTH, max(1, int(crop.height * CROP_MIN_WIDTH / crop.width))))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def ask_pair(crop: bytes, a: str, b: str, sample: bool = False) -> tuple[bool | None, float]:
    ask = tools._prompt("printed_toc_pair.md").replace("{a}", a).replace("{b}", b)
    try:
        data, seconds = tools._chat(
            [tools._image_part(crop), {"type": "text", "text": ask}],
            PAIR_TOKENS,
            schema=PAIR_SCHEMA,
            sample=sample,
        )
    except _VISION_ERRORS:
        return None, 0.0
    value = data.get("continues")
    return (value if isinstance(value, bool) else None), seconds


def votes_needed(new_item: bool) -> int:
    """A second line that opens like a new item needs every vote; otherwise a majority."""
    return VOTES if new_item else VOTES // 2 + 1


def arbitrate_join(
    book: Path,
    rows: list[dict[str, Any]],
    index: int,
    scale: tuple[float, float],
) -> tuple[bool, dict[str, Any], float]:
    """The page answer wants rows[index] joined to the row above. Decide with a closer look."""
    a, b = rows[index - 1], rows[index]
    record: dict[str, Any] = {"page": True, "new_item": starts_item(str(b["text"]))}
    crop = pair_crop(book, rows, a, b, scale)
    if crop is None:
        record["result"] = False
        record["needs_review"] = True
        return False, record, 0.0
    pair, seconds = ask_pair(crop, str(a["text"]), str(b["text"]))
    record["pair"] = pair
    if pair is True and not record["new_item"]:
        record["result"] = True
        return True, record, seconds
    votes = []
    for _ in range(VOTES):
        vote, spent = ask_pair(crop, str(a["text"]), str(b["text"]), sample=True)
        votes.append(vote)
        seconds += spent
    yes = sum(1 for vote in votes if vote is True)
    record["votes"] = votes
    record["result"] = yes >= votes_needed(record["new_item"])
    if 0 < yes < VOTES:
        record["needs_review"] = True
    return record["result"], record, seconds


# ---------------------------------------------------------------- 4 build


def _split_rows(row: dict[str, Any], parts: list[str]) -> list[dict[str, Any]]:
    y0, y1 = float(row["y0"]), float(row["y1"])
    span = (y1 - y0) / len(parts)
    out = []
    for index, part in enumerate(parts):
        item = dict(row)
        item["text"] = part
        item["chars"] = None
        item["y0"] = y0 + span * index
        item["y1"] = y0 + span * (index + 1)
        if index != len(parts) - 1:
            item["print_page"] = None
            item["hole"] = None
            item["hole_box"] = None
        out.append(item)
    return out


def build_entries(
    rows: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    joins: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rows of one page -> entries. Each entry remembers its decision."""
    out: list[dict[str, Any]] = []
    for index, (row, plan) in enumerate(zip(rows, plans)):
        join = joins.get(index)
        if join is not None and join["result"] and out:
            prev = out[-1]
            merged = layout._absorb(prev, row, layout._join(prev["text"], row["text"]))
            merged["decision"] = dict(prev["decision"])
            merged["decision"]["joins"] = list(prev["decision"].get("joins") or []) + [join]
            merged["decision"]["page_printed"] = plan["page_printed"]
            merged["decision"]["facts"] = list(prev["decision"]["facts"]) + plan["facts"]
            out[-1] = merged
            continue
        decision: dict[str, Any] = {
            "line": index + 1,
            "page_level": plan["level"],
            "page_printed": plan["page_printed"],
            "facts": list(plan["facts"]),
        }
        if join is not None:
            decision["joins"] = [join]
        pieces = _split_rows(row, plan["split"]) if plan["split"] else [dict(row)]
        for piece in pieces:
            piece["decision"] = dict(decision)
            if plan["split"]:
                piece["decision"]["split_from"] = row["text"]
                piece["decision"]["page_printed"] = plan["page_printed"] and piece is pieces[-1]
            out.append(piece)
    return out


# ---------------------------------------------------------------- 5 levels


def style_levels(entries: list[dict[str, Any]]) -> list[int | None]:
    """Print-style level of every text-layer entry, computed after joins and splits."""
    styled = [dict(entry) for entry in entries]
    if styled:
        layout.assign_levels(styled)
    return [int(s["level"]) if _text_layer(s) else None for s in styled]


def settle_level(page_level: int | None, style: int | None) -> tuple[int | None, str]:
    """Which level wins for one entry, and why.

    A measured print style always wins. Sampled re-asks repeated the model's own
    level every time, so they cannot overrule it; a differing answer is only flagged.
    """
    if style is None:
        return page_level, "page"
    if page_level is None or page_level == style:
        return style, "style"
    return style, "style_kept"


def clamp_levels(entries: list[dict[str, Any]]) -> None:
    """A catalog tree never skips a level going down."""
    prev = -1
    for entry in entries:
        level = int(entry.get("level") or 0)
        if level > prev + 1:
            entry["decision"]["level_clamped_from"] = level
            entry["level"] = prev + 1
            entry["level_conf"] = "low"
        prev = int(entry["level"])


# ---------------------------------------------------------------- 6 holes


def mark_printed_holes(entries: list[dict[str, Any]]) -> None:
    for entry in entries:
        if entry.get("print_page") is not None or entry.get("hole"):
            continue
        if not entry["decision"].get("page_printed"):
            continue
        height = max(8.0, float(entry["y1"]) - float(entry["y0"]))
        entry["hole"] = "printed"
        entry["hole_box"] = [
            float(entry["x1"]) + 4,
            float(entry["y0"]) - 4,
            float(entry["x1"]) + height * HOLE_WIDTH,
            float(entry["y1"]) + 4,
        ]


# ---------------------------------------------------------------- whole catalog


def _tail(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"text": e["text"], "level": e["decision"].get("page_level")} for e in entries[-TAIL:]]


def decide_catalog(
    book: Path,
    rows: list[dict[str, Any]],
    pages_meta: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], float]:
    """Catalog rows -> entries with levels, holes and decisions. Notes are per page."""
    scales = {int(m["page"]): tuple(m.get("scale") or (1.0, 1.0)) for m in pages_meta if m.get("page")}
    pages: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        pages.setdefault(int(row["pdf_page"]), []).append(row)
    entries: list[dict[str, Any]] = []
    notes: dict[int, dict[str, Any]] = {}
    spans: list[tuple[int, int, int]] = []
    seconds = 0.0
    for page, page_rows in pages.items():
        tail = _tail(entries)
        answers, spent = decide_page(book, page_rows, tail)
        seconds += spent
        plans = check_answers(page_rows, answers)
        joins: dict[int, dict[str, Any]] = {}
        for index, plan in enumerate(plans):
            if plan["continues"]:
                _, record, spent = arbitrate_join(book, page_rows, index, scales.get(page, (1.0, 1.0)))
                joins[index] = record
                seconds += spent
        start = len(entries)
        entries.extend(build_entries(page_rows, plans, joins))
        spans.append((page, start, len(entries)))
        notes[page] = {
            "read": answers is not None,
            "joins_asked": len(joins),
            "joined": sum(1 for j in joins.values() if j["result"]),
            "facts": sorted({f for plan in plans for f in plan["facts"]}),
        }
    styles = style_levels(entries)
    for page, start, end in spans:
        disputed = 0
        for i in range(start, end):
            entry = entries[i]
            level, why = settle_level(entry["decision"].get("page_level"), styles[i])
            entry["level"] = level if level is not None else int(entry.get("level") or 0)
            entry["level_from"] = why
            entry["level_conf"] = "high" if why in ("style", "page") else "low"
            entry["decision"]["style_level"] = styles[i]
            entry["decision"]["level_from"] = why
            if why == "style_kept":
                disputed += 1
                entry["decision"]["needs_review"] = True
            if any(j.get("needs_review") for j in entry["decision"].get("joins") or []):
                entry["decision"]["needs_review"] = True
        notes[page]["level_disputes"] = disputed
    clamp_levels(entries)
    mark_printed_holes(entries)
    return entries, notes, round(seconds, 3)
