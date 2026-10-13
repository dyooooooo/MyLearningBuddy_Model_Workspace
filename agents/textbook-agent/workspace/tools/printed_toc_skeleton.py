"""Two projections of body titles for the printed catalog.

sk_order: first sighting of each title, including running footers.
sk_expect: titles that also appear outside the page edge, plus slot names
this book already uses. Pure chrome (footer-only, repeated) stays out of
sk_expect so it cannot look like a missing catalog line.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_CHROME = {"header", "footer", "page_number", "page_footnote", "aside_text"}
_CHROME_MIN = 3
_MIN_KEY = 6
_SLOTS = (
    "Self-assessment",
    "Reading:",
    "Mini-project:",
    "Focus on language:",
    "Listening:",
    "Speaking:",
    "Video:",
)


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    return re.sub(r"[\W_]+", "", text)


def build_skeletons(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """items: page, text, chrome. Order of items is reading order."""
    groups: dict[str, dict[str, Any]] = {}
    order_keys: list[str] = []
    for item in items:
        text = str(item.get("text") or "").strip()
        key = norm(text)
        if len(key) < _MIN_KEY:
            continue
        found = groups.get(key)
        if found is None:
            groups[key] = found = {"text": text, "page": int(item["page"]), "chrome": 0, "body": 0}
            order_keys.append(key)
        if item.get("chrome"):
            found["chrome"] += 1
        else:
            found["body"] += 1
    sk_order = [{"text": groups[key]["text"], "page": groups[key]["page"]} for key in order_keys]
    sk_expect = []
    for key in order_keys:
        group = groups[key]
        if group["body"] == 0 and group["chrome"] >= _CHROME_MIN:
            continue
        if group["body"] >= 1:
            sk_expect.append({"text": group["text"], "page": group["page"]})
    return {"order": sk_order, "expect": sk_expect}


def add_seen_slots(sk_expect: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A slot name already printed under one unit belongs in the expected set."""
    have = {norm(item["text"]) for item in sk_expect}
    out = list(sk_expect)
    blob = "\n".join(str(row.get("text") or "") for row in rows)
    for slot in _SLOTS:
        key = norm(slot.rstrip(":"))
        if key in have:
            continue
        if norm(slot.rstrip(":")) and slot.rstrip(":") in blob or slot in blob:
            out.append({"text": slot.rstrip(":"), "page": 0, "slot": True})
            have.add(key)
    return out


def title_hits(label: str, titles: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    """Non-overlapping hits of skeleton titles inside one catalog label. Longest first."""
    raw = label or ""
    folded = raw.casefold()
    found: list[tuple[int, int, str]] = []
    ranked = sorted(titles, key=lambda item: len(item["text"]), reverse=True)
    for item in ranked:
        needle = str(item["text"]).casefold().strip()
        if len(norm(needle)) < _MIN_KEY:
            continue
        start = 0
        while True:
            at = folded.find(needle, start)
            if at < 0:
                break
            end = at + len(needle)
            if any(not (end <= left or at >= right) for left, right, _ in found):
                start = at + 1
                continue
            found.append((at, end, str(item["text"])))
            start = end
    found.sort()
    return found


def split_on_hits(label: str, titles: list[dict[str, Any]]) -> list[str] | None:
    """Split a label that contains two or more skeleton titles.

    A slot prefix such as ``Reading:`` is one entry with the words after it,
    not a second title.
    """
    usable = [
        item for item in titles
        if len(norm(item["text"])) >= 12 and not str(item["text"]).rstrip().endswith(":")
    ]
    hits = title_hits(label, usable)
    if len(hits) < 2:
        return None
    parts: list[str] = []
    for index, (start, _end, _name) in enumerate(hits):
        stop = hits[index + 1][0] if index + 1 < len(hits) else len(label)
        piece = label[start:stop].strip(" ·,;/")
        lead = label[:start].strip()
        if index == 0 and lead:
            piece = (lead + " " + piece).strip()
        if piece:
            parts.append(piece)
    if len(parts) < 2:
        return None
    return parts


def units_of(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    units: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if re.match(r"^\d+\s+\S", str(row.get("text") or "")) and current:
            units.append(current)
            current = [row]
        else:
            current.append(row)
    if current:
        units.append(current)
    return units


def slot_gaps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Slots present in some units and missing in others."""
    units = [unit for unit in units_of(rows) if unit and re.match(r"^\d+\s+\S", str(unit[0].get("text") or ""))]
    if len(units) < 2:
        return []
    gaps = []
    for slot in _SLOTS:
        key = norm(slot.rstrip(":"))
        owners = []
        for index, unit in enumerate(units):
            if any(key in norm(str(row.get("text") or "")) for row in unit):
                owners.append(index)
        if not owners:
            continue
        for index, unit in enumerate(units):
            if index in owners:
                continue
            gaps.append({
                "unit": str(unit[0].get("text") or ""),
                "slot": slot.rstrip(":"),
                "pdf_page": unit[-1].get("pdf_page"),
                "col": unit[-1].get("col"),
                "x0": unit[-1].get("x0"),
                "x1": unit[-1].get("x1"),
                "y0": unit[-1].get("y0"),
                "y1": unit[-1].get("y1"),
            })
    return gaps


def slot_level(rows: list[dict[str, Any]], slot: str) -> int | None:
    key = norm(slot.rstrip(":"))
    levels = [int(row["level"]) for row in rows if key in norm(str(row.get("text") or "")) and row.get("level") is not None]
    if not levels:
        return None
    return max(set(levels), key=levels.count)


def clear_backward_pages(rows: list[dict[str, Any]]) -> int:
    """A page number smaller than the previous one in the same column is not a page. Returns how many were cleared."""
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(int(row.get("col") or 0), []).append(row)
    cleared = 0
    for group in groups.values():
        last: int | None = None
        for row in group:
            page = row.get("print_page")
            if not isinstance(page, int):
                continue
            if last is not None and page < last:
                height = max(8.0, float(row["y1"]) - float(row["y0"]))
                row["print_page"] = None
                row.pop("filled", None)
                row["hole"] = "backward"
                row["hole_box"] = [
                    float(row["x1"]) + 4,
                    float(row["y0"]) - 4,
                    float(row["x1"]) + height * 8,
                    float(row["y1"]) + 4,
                ]
                cleared += 1
                continue
            last = page
    return cleared
