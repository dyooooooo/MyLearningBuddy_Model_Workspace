"""Rule-based page merge and same-page detection (B layer logic)."""

from __future__ import annotations

from difflib import SequenceMatcher

from app.schemas import MergeDiff, PagePart

SAME_PAGE_THRESHOLD = 0.55


def _text(a: str) -> str:
    return " ".join((a or "").split())


def _similarity(a: str, b: str) -> float:
    a, b = _text(a), _text(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _header_section_key(parts: list[PagePart]) -> str:
    bits: list[str] = []
    for p in parts:
        if p.kind in ("header", "section"):
            bits.append(_text(p.print_text))
    return "|".join(bits)


def _question_print_map(parts: list[PagePart]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in parts:
        if p.kind == "question" and p.id:
            out[p.id] = _text(p.print_text)
    return out


def same_page_score(base: list[PagePart], incoming: list[PagePart]) -> float:
    if not base:
        return 1.0
    hs = _similarity(_header_section_key(base), _header_section_key(incoming))
    bq = _question_print_map(base)
    iq = _question_print_map(incoming)
    if not bq and not iq:
        return hs
    if not bq or not iq:
        return hs * 0.5
    common = set(bq) & set(iq)
    if not common:
        return min(hs, 0.2)
    print_scores = [_similarity(bq[k], iq[k]) for k in common]
    overlap = len(common) / max(len(bq), len(iq))
    avg_print = sum(print_scores) / len(print_scores)
    return 0.35 * hs + 0.45 * avg_print + 0.2 * overlap


def is_same_page(base: list[PagePart], incoming: list[PagePart]) -> tuple[bool, float]:
    score = same_page_score(base, incoming)
    return score >= SAME_PAGE_THRESHOLD, score


def _longer(a: str, b: str) -> str:
    return a if len(_text(a)) >= len(_text(b)) else b


def _has_handwriting_bbox(p: PagePart) -> bool:
    bb = p.bbox_handwriting or []
    return len(bb) >= 4


def _merge_question_handwriting(cur: PagePart, incoming: PagePart) -> None:
    """Only adopt incoming handwriting when vision grounded it with a bbox."""
    incoming_hw = _text(incoming.handwriting)
    if not incoming_hw:
        return
    if not _has_handwriting_bbox(incoming):
        return
    cur.handwriting = _longer(cur.handwriting, incoming.handwriting)
    cur.bbox_handwriting = list(incoming.bbox_handwriting)


def merge_parts(base: list[PagePart], incoming: list[PagePart]) -> list[PagePart]:
    if not base:
        return [p.model_copy(deep=True) for p in incoming]
    by_kind: dict[tuple[str, str | None], PagePart] = {}
    order: list[tuple[str, str | None]] = []

    def key(p: PagePart) -> tuple[str, str | None]:
        return (p.kind, p.id if p.kind == "question" else None)

    for p in base:
        k = key(p)
        by_kind[k] = p.model_copy(deep=True)
        if k not in order:
            order.append(k)

    for p in incoming:
        k = key(p)
        if k not in by_kind:
            by_kind[k] = p.model_copy(deep=True)
            order.append(k)
            continue
        cur = by_kind[k]
        cur.print_text = _longer(cur.print_text, p.print_text)
        if p.kind == "question":
            _merge_question_handwriting(cur, p)
        if p.bbox_print:
            cur.bbox_print = list(p.bbox_print)

    return [by_kind[k] for k in order]


def diff_parts(base: list[PagePart], merged: list[PagePart]) -> MergeDiff:
    base_ids = {p.id for p in base if p.kind == "question" and p.id}
    merged_ids = {p.id for p in merged if p.kind == "question" and p.id}
    new_ids = sorted(merged_ids - base_ids)

    base_hw = {p.id: len(_text(p.handwriting)) for p in base if p.kind == "question" and p.id}
    growth: dict[str, int] = {}
    for p in merged:
        if p.kind != "question" or not p.id:
            continue
        old_len = base_hw.get(p.id, 0)
        new_len = len(_text(p.handwriting))
        delta = new_len - old_len
        if delta > 0:
            growth[p.id] = delta

    return MergeDiff(new_question_ids=new_ids, handwriting_growth=growth)


def has_handwriting_growth(base: list[PagePart], incoming: list[PagePart]) -> bool:
    diff = diff_parts(base, merge_parts(base, incoming))
    return bool(diff.new_question_ids or diff.handwriting_growth)
