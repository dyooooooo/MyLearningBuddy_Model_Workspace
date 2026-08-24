"""MinerU 全书导入会话状态：open_issues 与意图。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.segment_state import (  # noqa: E402
    _locate_evidence,
    apply_qa_report_to_payload,
    autofix_may_finish,
    batch_actionable_findings,
    has_blocking_open_issues,
    mark_issues_status,
    merge_open_issues,
    note_imported_through,
    open_issue_pages,
    remaining_actionable_findings,
    remaining_as_open_issues,
    symptom_hints_instruction,
    user_accepts_suggestions,
    user_wants_skip_and_continue,
)


def test_open_issues_gate():
    pl = {"open_issues": [{"page": 3, "kind": "layout", "status": "open"}]}
    assert has_blocking_open_issues(pl)
    assert open_issue_pages(pl) == [3]
    pl["open_issues"] = mark_issues_status(pl, status="skipped_by_user")
    assert not has_blocking_open_issues(pl)


def test_merge_keeps_prior_issues():
    pl = {
        "open_issues": [
            {"page": 5, "kind": "formula", "status": "open", "batch": "1-178"}
        ]
    }
    merged = merge_open_issues(
        pl, [{"page": 25, "kind": "layout", "status": "open", "batch": "1-178"}]
    )
    pages = sorted(int(x["page"]) for x in merged if x.get("status") == "open")
    assert pages == [5, 25]


def test_skip_intent():
    assert user_wants_skip_and_continue("请把全文继续")
    assert user_wants_skip_and_continue("不用改，继续")
    assert user_wants_skip_and_continue("继续完成全文导入任务")
    assert user_wants_skip_and_continue("全文识别")
    assert user_accepts_suggestions("按建议改")
    assert user_accepts_suggestions("开始改")
    assert not user_wants_skip_and_continue("按你的建议改")


def test_note_imported_through_monotonic():
    pl: dict = {"page_count": 178}
    note_imported_through(pl, 20)
    note_imported_through(pl, 178)
    assert pl["segment_imported_through"] == 178
    note_imported_through(pl, 40)
    assert pl["segment_imported_through"] == 178


def test_symptom_hints_carry_steps_not_paths():
    """playbook 档给 Agent 的必须是步骤本身；同症状多处只列一份手册。"""
    steps = "1. 定位公式块\n2. 中文拆到公式外"
    known = [
        {
            "suspect_type": "CJK_OR_UNIT_IN_LATEX",
            "page_no": p,
            "remediation_kind": "playbook",
            "playbook_ref": "configs/checks/cjk_or_unit_in_latex.yaml#remediation.playbook",
            "playbook": steps,
        }
        for p in (12, 13)
    ]
    out = symptom_hints_instruction({"known": known, "unknown": []})
    assert "2. 中文拆到公式外" in out
    assert out.count("1. 定位公式块") == 1, "同一症状的手册不该逐处重复"


def test_locate_evidence_keeps_only_the_declared_locating_fields():
    """整个 evidence 撑爆上下文，全丢掉又不知道改哪一块——按 locate_by 取子集。"""
    located = _locate_evidence(
        {
            "locate_by": ["zone", "rel_x0"],
            "evidence": {
                "zone": "main",
                "rel_x0": 0.72,
                "width_ratio": 0.21,
                "text_preview": "图 1-3 单位圆",
            },
        }
    )
    assert located == {"zone": "main", "rel_x0": 0.72}


def test_locate_evidence_falls_back_to_whole_evidence():
    """老数据没存 locate_by：宁可多给几个字段，也别让改进器没有定位依据。"""
    located = _locate_evidence({"evidence": {"zone": "aside", "level": 1}})
    assert located == {"zone": "aside", "level": 1}
    assert _locate_evidence({"evidence": {}}) == {}


def test_locate_evidence_caps_size():
    evidence = {f"f{i}": "长" * 300 for i in range(12)}
    located = _locate_evidence({"evidence": evidence})
    assert len(located) <= 6
    assert all(len(v) <= 120 for v in located.values())


def test_symptom_hints_skip_playbook_block_when_unresolved():
    out = symptom_hints_instruction(
        {
            "known": [
                {
                    "suspect_type": "X_SYMPTOM",
                    "page_no": 3,
                    "playbook_ref": "configs/checks/x.yaml#remediation.playbook",
                }
            ],
            "unknown": [],
        }
    )
    assert "手册步骤（" not in out


def test_autofix_gate_ignores_false_positives_and_batches_one_type():
    data = {
        "known": [
            {"suspect_type": "MATH_FALLBACK", "page_no": 60, "false_positive_hits": 0},
            {"suspect_type": "MATH_FALLBACK", "page_no": 61},
            {"suspect_type": "ASIDE_FIGURE_MISPLACED_IN_MAIN", "page_no": 12},
            {"suspect_type": "CJK_OR_UNIT_IN_LATEX", "page_no": 3, "false_positive_hits": 2},
        ],
        "unknown": [{"suspect_type": "NEW_THING", "page_no": 9}],
    }
    left = remaining_actionable_findings(data)
    assert [f["page_no"] for f in left] == [60, 61, 12]
    assert not autofix_may_finish(data)
    batch = batch_actionable_findings(left)
    assert [f["suspect_type"] for f in batch] == ["MATH_FALLBACK", "MATH_FALLBACK"]
    assert autofix_may_finish({"known": [], "unknown": data["unknown"]})
    issues = remaining_as_open_issues(left, batch="1-178")
    assert {it["page"] for it in issues} == {60, 61, 12}


def test_symptom_hints_state_remaining_not_silent_drop():
    """171 处只塞 40 条进提示且不说还剩多少，是上次提前收口的根因之一。"""
    known = [
        {
            "suspect_type": "MATH_FALLBACK",
            "page_no": p,
            "playbook": "修公式再 rerender",
        }
        for p in range(1, 50)
    ]
    out = symptom_hints_instruction({"known": known, "unknown": []})
    assert "还剩 49 处" in out
    assert "交 JSON 报告本身不能收口" in out
    assert out.count('"page_no"') <= 24


def test_apply_qa_report_marks_fixed():
    pl = {
        "open_issues": [
            {"page": 8, "kind": "layout", "status": "open"},
            {"page": 12, "kind": "formula", "status": "open"},
        ]
    }
    apply_qa_report_to_payload(
        pl,
        {"fixed_pages": [8], "issues": [], "needs_user": False},
        batch="1-178",
    )
    assert open_issue_pages(pl) == [12]
