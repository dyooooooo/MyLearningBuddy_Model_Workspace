"""Tests for eval bank answer lookup and reference/handwriting guard."""

from __future__ import annotations

from app.agents.eval_bank import answers_too_similar, lookup_bank_answer, normalize_answer
from app.rag.retriever import EvalContext


def test_normalize_answer_strips_spaces():
    assert normalize_answer("(-∞, -1]") == normalize_answer("(-∞,-1]")


def test_answers_too_similar_detects_copy():
    assert answers_too_similar("x<2 或 x>3", "x<2或 x>3") is True
    assert answers_too_similar("(-∞,-1]", "x=5") is False


def test_lookup_bank_answer_from_jsonl():
    ctx = EvalContext(
        query="已知全集 U=(-∞, 1)，集合 A=(-1, 1)，则 A 的补集为",
        subject="math",
        grade_band="hs",
    )
    ans, src, score = lookup_bank_answer(
        "已知全集 U=(-∞, 1)，集合 A=(-1, 1)，则 A 的补集为",
        ctx,
    )
    assert ans == "(-∞,-1]"
    assert "question_bank" in src
    assert score >= 0.75
