"""Question-bank answer lookup and reference vs handwriting sanity checks."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from app.config import settings
from app.rag.retriever import EvalContext

_ANSWER_NORM_RE = re.compile(r"[\s,，;；]+")


def normalize_answer(text: str) -> str:
    s = (text or "").strip().lower()
    s = s.replace("（", "(").replace("）", ")").replace("∞", "+∞")
    return _ANSWER_NORM_RE.sub("", s)


def answers_too_similar(reference: str, handwriting: str, *, threshold: float = 0.88) -> bool:
    """True when reference is essentially the student's handwriting (eval bug guard)."""
    a = normalize_answer(reference)
    b = normalize_answer(handwriting)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 4 and (a in b or b in a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _stem_similarity(stem: str, print_text: str) -> float:
    a = normalize_answer(stem)
    b = normalize_answer(print_text)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _answer_from_bucket(item: dict) -> str | None:
    meta = item.get("meta") or {}
    ans = (meta.get("answer") or "").strip()
    return ans or None


def lookup_bank_answer(print_text: str, ctx: EvalContext) -> tuple[str | None, str, float]:
    """Return (canonical answer, source tag, stem match score 0–1)."""
    best_ans: str | None = None
    best_score = 0.0
    best_src = ""

    for bucket_name, items in (
        ("question_bank", getattr(ctx, "questions", []) or []),
        ("chunk", ctx.chunks),
        ("method", ctx.methods),
    ):
        for item in items:
            ans = _answer_from_bucket(item)
            if not ans:
                continue
            stem = item.get("summary") or item.get("text") or item.get("label") or ""
            score = _stem_similarity(stem, print_text)
            if score > best_score:
                best_score = score
                best_ans = ans
                best_src = bucket_name

    for hit in ctx.hits:
        if hit.source != "question_bank" and "question" not in hit.source:
            continue
        score = _stem_similarity(hit.text, print_text)
        if score > best_score:
            ans = _lookup_jsonl_answer(print_text, min_ratio=max(0.55, score - 0.1))
            if ans:
                best_score = score
                best_ans = ans
                best_src = "question_bank_hit"

    if best_ans and best_score >= 0.45:
        return best_ans, best_src, best_score

    jsonl_ans, js_score = _lookup_jsonl_answer_scored(print_text, min_ratio=0.62)
    if jsonl_ans:
        return jsonl_ans, "question_bank_jsonl", js_score
    return None, "", 0.0


def _lookup_jsonl_answer_scored(print_text: str, *, min_ratio: float) -> tuple[str | None, float]:
    path = Path(settings.corpus_dir) / "question-bank" / "math_hs.jsonl"
    if not path.is_file():
        return None, 0.0
    best: str | None = None
    best_score = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        stem = row.get("stem") or ""
        ans = (row.get("answer") or "").strip()
        if not stem or not ans:
            continue
        score = _stem_similarity(stem, print_text)
        if score > best_score and score >= min_ratio:
            best_score = score
            best = ans
    return best, best_score


def _lookup_jsonl_answer(print_text: str, *, min_ratio: float) -> str | None:
    ans, _ = _lookup_jsonl_answer_scored(print_text, min_ratio=min_ratio)
    return ans
