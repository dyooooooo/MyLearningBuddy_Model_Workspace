"""Tests for cursor pack triage heuristics."""
from __future__ import annotations

from app.import_35b.cursor_pack.triage import triage_feedback


def test_triage_main_empty_high_compiler():
    r = triage_feedback(
        t1_snapshot=[{"suspect_type": "MAIN_EMPTY", "severity": "high"}],
        user_body="整页没内容",
    )
    assert r.layer == "compiler"
    assert r.confidence == "high"
    assert r.plan_mode == "execute_direct"
    assert "page_compiler.py" in r.suggested_files[0]


def test_triage_control_char_boundary():
    r = triage_feedback(
        t1_snapshot=[{"suspect_type": "CONTROL_CHAR_RESIDUE"}],
        user_body="",
    )
    assert r.layer == "boundary"
    assert r.confidence == "high"


def test_triage_user_only_low_harness():
    r = triage_feedback(t1_snapshot=[], user_body="公式显示不对")
    assert r.layer == "harness"
    assert r.confidence == "low"
    assert r.entry_doc == "PLAN.prompt.md"
    assert r.plan_mode == "refine_first"
