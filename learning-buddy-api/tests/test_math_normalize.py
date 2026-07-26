from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.math.enrich import enrich_eval_with_rich
from app.math.normalize import normalize_plain

GOLDEN = Path(__file__).resolve().parents[1] / "data" / "eval" / "math_segments_golden.jsonl"


def _load_golden() -> list[dict]:
    rows = []
    if not GOLDEN.is_file():
        return rows
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


@pytest.mark.parametrize("case", _load_golden(), ids=lambda c: c.get("id", "?"))
def test_math_segments_golden(case: dict) -> None:
    rich = normalize_plain(case["raw"])
    assert rich.raw == case["raw"]
    math_segs = [s for s in rich.segments if s.kind == "math"]
    if case.get("expect_valid"):
        assert any(s.valid for s in math_segs), f"expected valid math segment: {rich}"
        latex_blob = " ".join(s.latex or "" for s in math_segs if s.valid)
        for needle in case.get("latex_contains") or []:
            assert needle in latex_blob, f"missing {needle!r} in {latex_blob!r}"
    else:
        assert all(not s.valid for s in math_segs) or not math_segs


def test_enrich_eval_with_rich_adds_fields() -> None:
    ev = enrich_eval_with_rich(
        {
            "reference_answer": "(-∞,-1]",
            "solution_steps": ["综合得 A̅ = (-∞,-1]。"],
            "reasoning_steps": ["与标准答一致"],
            "match_notes": "等价",
        }
    )
    assert "reference_answer_rich" in ev
    assert ev["reference_answer_rich"]["raw"] == "(-∞,-1]"
    assert len(ev["solution_steps_rich"]) == 1
    assert len(ev["reasoning_steps_rich"]) == 1
    assert "match_notes_rich" in ev


def test_cjk_splits_to_text_and_math() -> None:
    rich = normalize_plain("由题意得 2r+αr=10")
    kinds = [s.kind for s in rich.segments]
    assert "text" in kinds
    assert "math" in kinds
