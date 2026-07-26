from unittest.mock import patch

from app.agents.eval_agent import (
    _extract_answer_from_steps,
    _finalize_reference,
    _reference_violates_universe,
)
from app.config import settings


def test_extract_complement_from_steps():
    steps = [
        "明确全集 U=(-∞,1)，集合 A=(-1,1)。",
        "综合得 A̅ = (-∞,-1]。",
    ]
    assert _extract_answer_from_steps(steps) == "(-∞,-1]"


def test_universe_violation_detects_extra_interval():
    assert _reference_violates_universe(
        "(-∞,-1]∪[1,+∞)",
        "已知全集 U=(-∞,1)，集合 A=(-1,1)，则 A 的补集为",
    )


def test_universe_violation_detects_union_one():
    assert _reference_violates_universe(
        "(-∞,-1]∪{1}",
        "已知全集 U=(-∞,1)，集合 A=(-1,1)，则 A 的补集为",
    )


def test_finalize_prefers_bank_on_high_match():
    ref = {
        "reference_answer": "(-∞,-1]∪[1,+∞)",
        "solution_steps": ["先求 A 的边界。"],
        "reference_source": "agent_solved",
    }
    with patch.object(settings, "eval_use_bank_answer", True):
        out = _finalize_reference(
            ref,
            print_text="已知全集 U=(-∞,1)，集合 A=(-1,1)，则 A 的补集为",
            bank_answer="(-∞,-1]",
            bank_source="question_bank_jsonl",
            bank_score=0.95,
        )
    assert out["reference_answer"] == "(-∞,-1]"
    assert "question_bank" in out["reference_source"]


def test_finalize_without_bank_keeps_format_reference():
    ref = {
        "reference_answer": "(-∞,-1]",
        "solution_steps": ["综合得 A̅ = (-∞,-1]∪[1,+∞)。"],
        "reference_source": "agent_solved",
    }
    with patch.object(settings, "eval_use_bank_answer", False):
        out = _finalize_reference(
            ref,
            print_text="已知全集 U=(-∞,1)，集合 A=(-1,1)，则 A 的补集为",
            bank_answer="(-∞,-1]",
            bank_source="question_bank_jsonl",
            bank_score=0.95,
        )
    assert out["reference_answer"] == "(-∞,-1]"
