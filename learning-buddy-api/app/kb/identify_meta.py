"""Guess subject / stage / title from MinerU text of the first pages."""

from __future__ import annotations

import re
from typing import Any

from app.kb.markdown import _TITLE_TYPES, _plain_text

SUBJECTS: tuple[tuple[str, str], ...] = (
    ("数学", "math"),
    ("语文", "chinese"),
    ("物理", "physics"),
    ("化学", "chemistry"),
    ("生物", "biology"),
    ("英语", "english"),
)

_GRADE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"普通高中|高中"), "hs"),
    (re.compile(r"七年级|八年级|九年级|初中"), "ms"),
    (re.compile(r"小学|[一二三四五六]年级"), "ps"),
    (re.compile(r"义务教育"), "ms"),
)

_TITLE_LINE = re.compile(r"(普通高中教科书|义务教育教科书|小学教科书)[^\n]{0,48}")
_VOLUME = re.compile(r"(选择性)?必修\s*第?[一二三四五六七八九十0-9]+册")
_GRADE_VOL = re.compile(r"([一二三四五六七八九]年级)\s*[上下]册")


def _block_text(blocks: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        text = _plain_text(block)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _title_lines(blocks: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "") not in _TITLE_TYPES:
            continue
        text = _plain_text(block)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _pick_subject(head: str, full: str) -> str:
    for zh, sid in SUBJECTS:
        if zh in head:
            return sid
    for zh, sid in SUBJECTS:
        if zh in full:
            return sid
    return "math"


def _pick_grade(blob: str) -> str:
    for rx, gid in _GRADE_RULES:
        if rx.search(blob):
            return gid
    return "hs"


def _volume_suffix(blob: str) -> str:
    vol = _VOLUME.search(blob)
    if vol:
        return re.sub(r"\s+", "", vol.group(0))
    grade = _GRADE_VOL.search(blob)
    if grade:
        return re.sub(r"\s+", "", grade.group(0))
    return ""


def _series(blob: str) -> str:
    if "普通高中" in blob:
        return "普通高中教科书"
    if "小学教科书" in blob:
        return "小学教科书"
    if "义务教育" in blob:
        return "义务教育教科书"
    if re.search(r"(?<![义普])高中", blob):
        return "普通高中教科书"
    if "小学" in blob:
        return "小学教科书"
    return ""


def _pick_label(blob: str, subject: str, fallback: str) -> str:
    zh = next((name for name, sid in SUBJECTS if sid == subject), "")
    series = _series(blob)
    suffix = _volume_suffix(blob)
    if series:
        mid = f"·{zh}" if zh else ""
        return f"{series}{mid}{suffix}"
    match = _TITLE_LINE.search(blob)
    if match:
        label = re.sub(r"\s+", "", match.group(0))
        label = re.sub(r"[A-Za-z]{2,}", "", label)
        if zh and zh not in label and "教科书" in label:
            label = label.replace("教科书", f"教科书·{zh}", 1)
        if suffix and suffix not in label:
            label = f"{label}{suffix}"
        return label
    for line in blob.splitlines():
        one = re.sub(r"\s+", "", line.strip())
        if "教科书" in one and 4 <= len(one) <= 48:
            return one
    label = (fallback or "").strip()
    if label.lower().endswith(".pdf"):
        return label[:-4]
    return label


def _hint_from_label(label: str) -> str:
    hint = re.sub(r"^(普通高中|义务教育|小学)?教科书[·・•\s]*", "", label)
    return hint or label


def guess_import_meta(
    markdown: str,
    blocks: list[dict[str, Any]] | None = None,
    *,
    fallback_label: str = "",
    fallback_hint: str = "",
) -> dict[str, str]:
    titles = _title_lines(blocks)
    body = "\n".join(part for part in (markdown or "", _block_text(blocks)) if part)
    blob = "\n".join(part for part in (titles, body) if part)
    head = "\n".join(blob.splitlines()[:40])
    subject = _pick_subject(head, blob)
    label = _pick_label(blob, subject, fallback_label or fallback_hint)
    hint = _hint_from_label(label) or (fallback_hint or label)
    if hint.lower().endswith(".pdf"):
        hint = label
    return {
        "label": label,
        "textbook_hint": hint,
        "subject": subject,
        "grade_band": _pick_grade(blob),
    }
