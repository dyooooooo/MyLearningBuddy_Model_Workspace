"""Stable Obsidian-style block IDs for fence re-anchoring.

IDs live in the official page text (`pages/*.md`) as ` ^kb-xxxxxx` (or a
standalone `^kb-xxxxxx` line after a table). The mid-layer fence keeps the
same markers inside its inner text. `paste_fences` looks up those markers
instead of the whole inner string, so a one-character typo no longer drops
the fence.

This module never writes files. The stamp tool is the only writer.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

ID_PREFIX = "kb-"
ID_BODY = r"[0-9a-f]{6}"
ID = ID_PREFIX + ID_BODY

# Trailing marker: one space, caret, our id, optional trailing spaces, end of line.
_TRAILING = re.compile(rf" \^({ID})[ \t]*$", re.I | re.M)
# Table / structured block: the id sits on its own line.
_STANDALONE = re.compile(rf"^\^({ID})[ \t]*$", re.I | re.M)
# Either form, for extraction in appearance order.
_ANY = re.compile(rf"(?:(?<=\s)|^)\^({ID})(?=\s|$)", re.I)

_TABLE_LINE = re.compile(r"^\s*(?:\||<table|<thead|<tbody|<tr)\b", re.I)


def extract_ids(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _ANY.finditer(text or ""):
        bid = match.group(1).lower()
        if bid not in seen:
            seen.add(bid)
            out.append(bid)
    return out


def strip_ids(text: str) -> str:
    """Remove our markers only. Math `$^{n}$` and footnotes `[^1]` stay."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        if _STANDALONE.match(line):
            continue
        lines.append(_TRAILING.sub("", line))
    body = "\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body)
    if (text or "").endswith("\n") and body:
        body += "\n"
    return body


def alloc_id(used: set[str], seed: str) -> str:
    n = 0
    while True:
        digest = hashlib.sha256(f"{seed}:{n}".encode("utf-8")).hexdigest()[:6]
        cand = f"{ID_PREFIX}{digest}"
        if cand not in used:
            used.add(cand)
            return cand
        n += 1


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, content) per line. `end` is the index of `\\n` or len(text)."""
    out: list[tuple[int, int, str]] = []
    start = 0
    for i, ch in enumerate(text):
        if ch == "\n":
            out.append((start, i, text[start:i]))
            start = i + 1
    out.append((start, len(text), text[start:]))
    return out


def _blocks(text: str) -> list[tuple[int, int]]:
    """[start, end) of each run of non-empty lines. `end` is at the line's `\\n` or EOF."""
    blocks: list[tuple[int, int]] = []
    lines = _line_spans(text)
    i = 0
    while i < len(lines):
        if not lines[i][2].strip():
            i += 1
            continue
        start = lines[i][0]
        j = i
        while j < len(lines) and lines[j][2].strip():
            j += 1
        blocks.append((start, lines[j - 1][1]))
        i = j
    return blocks


def _is_table_block(chunk: str) -> bool:
    return any(_TABLE_LINE.match(line) for line in chunk.splitlines())


def _attach_at_block(text: str, span: tuple[int, int], bid: str) -> str:
    start, end = span
    chunk = text[start:end]
    last = chunk.split("\n")[-1]
    if _STANDALONE.match(last) or _TRAILING.search(last):
        return text
    if _is_table_block(chunk):
        insert = f"\n\n^{bid}"
        return text[:end] + insert + text[end:]
    return text[:end] + f" ^{bid}" + text[end:]


def attach_ids(chunk: str, used: set[str], seed: str) -> tuple[str, list[str]]:
    existing = extract_ids(chunk)
    if existing:
        used.update(existing)
        return chunk, existing
    blocks = _blocks(chunk)
    if not blocks:
        return chunk, []
    if len(blocks) == 1:
        bid = alloc_id(used, f"{seed}:0")
        return _attach_at_block(chunk, blocks[0], bid), [bid]
    start_id = alloc_id(used, f"{seed}:s")
    stamped = _attach_at_block(chunk, blocks[0], start_id)
    end_id = alloc_id(used, f"{seed}:e")
    stamped = _attach_at_block(stamped, _blocks(stamped)[-1], end_id)
    return stamped, [start_id, end_id]


def _marker_pos(source: str, bid: str) -> int:
    trail = source.find(f" ^{bid}")
    if trail >= 0:
        return trail
    standalone = re.search(rf"^\^{re.escape(bid)}[ \t]*$", source, re.M)
    if standalone:
        return standalone.start()
    return -1


def locate_id(source: str, bid: str) -> tuple[int, int] | None:
    """`[start, end)` of the content this id marks, including the marker.

    Trailing IDs partition a blank-line block: each marker owns the text
    after the previous marker (or the block start) through its own line.
    That keeps consecutive `kb-list-item` fences from claiming the whole
    list. A standalone `^id` line (tables) marks the block above it.
    """
    pos = _marker_pos(source, bid)
    if pos < 0:
        return None
    blocks = _blocks(source)
    for i, (bstart, bend) in enumerate(blocks):
        if not (bstart <= pos <= bend):
            continue
        line = source[bstart:bend].strip()
        if _STANDALONE.match(line) and i > 0:
            return (blocks[i - 1][0], bend)
        line_end = source.find("\n", pos)
        if line_end < 0:
            line_end = len(source)
        region = source[bstart:pos]
        prev = [bstart + m.end() for m in _TRAILING.finditer(region)]
        prev += [bstart + m.end() for m in _STANDALONE.finditer(region)]
        if prev:
            last = max(prev)
            nl = source.find("\n", last)
            start = nl + 1 if 0 <= nl < pos else last
        else:
            start = bstart
        return (start, line_end)
    return None


def find_id_span(source: str, ids: list[str]) -> tuple[int, int] | None:
    """Span covering every listed id's block. All ids must resolve."""
    if not ids:
        return None
    spans: list[tuple[int, int]] = []
    for bid in ids:
        found = locate_id(source, bid)
        if found is None:
            return None
        spans.append(found)
    return (min(s[0] for s in spans), max(s[1] for s in spans))


def stamp_text(source: str, mid: str, *, seed: str) -> tuple[str, str, dict[str, Any]]:
    """Add missing IDs to a source/mid pair. Pure function; caller writes."""
    from app.page_sync import _find_block, _segments, page_bytes

    used = set(extract_ids(source)) | set(extract_ids(mid))
    segs = _segments(mid)
    jobs: list[tuple[int, int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    skipped = stamped = already = 0

    def _overlaps(start: int, end: int) -> bool:
        return any(not (end <= left or right <= start) for left, right in occupied)

    def _find_free(needle: str) -> int:
        cursor = 0
        while True:
            pos = _find_block(source, needle, cursor)
            if pos < 0:
                return -1
            end = pos + len(needle)
            if not _overlaps(pos, end):
                return pos
            cursor = pos + 1

    for idx, (kind, name, payload) in enumerate(segs):
        if kind != "fence" or not (payload or "").strip():
            continue
        have = extract_ids(payload)
        if have and all(_marker_pos(source, bid) >= 0 for bid in have):
            already += 1
            span = find_id_span(source, have)
            if span is not None:
                occupied.append(span)
            continue
        needle = page_bytes(strip_ids(payload) if have else payload).rstrip("\n")
        if not needle:
            skipped += 1
            continue
        pos = _find_free(needle)
        if pos < 0:
            skipped += 1
            continue
        end = pos + len(needle)
        occupied.append((pos, end))
        jobs.append((pos, end, idx, name))

    jobs.sort(key=lambda row: row[0], reverse=True)
    new_source = source
    new_payloads: dict[int, str] = {}
    for pos, end, idx, name in jobs:
        chunk = new_source[pos:end]
        filled, _ids = attach_ids(chunk, used, f"{seed}:{name}:{pos}")
        if filled == chunk:
            continue
        new_source = new_source[:pos] + filled + new_source[end:]
        new_payloads[idx] = filled
        stamped += 1

    if new_payloads:
        parts: list[str] = []
        for idx, (kind, name, payload) in enumerate(segs):
            if kind == "prose":
                parts.append(payload)
            else:
                inner = new_payloads.get(idx, payload)
                parts.append(f"::: {name}\n{inner.rstrip()}\n:::")
        from app.page_sync import page_bytes as _pb

        new_mid = _pb("".join(parts))
    else:
        new_mid = mid

    return new_source, new_mid, {
        "stamped": stamped,
        "already": already,
        "skipped": skipped,
        "changed": new_source != source or new_mid != mid,
    }
