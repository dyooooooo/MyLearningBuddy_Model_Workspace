"""MinerU 分段状态：门闸与意图。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.segment_state import (  # noqa: E402
    IMPORT_PAGES_PER_SEGMENT,
    advance_past_gate_batch,
    apply_qa_report_to_payload,
    has_blocking_open_issues,
    imported_through,
    mark_issues_status,
    merge_open_issues,
    note_imported_through,
    open_issue_pages,
    segment_batches,
    set_gate_batch,
    user_accepts_suggestions,
    user_wants_skip_and_continue,
)


def test_batch_size_20():
    assert IMPORT_PAGES_PER_SEGMENT == 20
    batches = segment_batches(178)
    assert batches[0] == (1, 20)
    assert batches[-1][1] == 178


def test_open_issues_gate():
    pl = {"open_issues": [{"page": 3, "kind": "layout", "status": "open"}]}
    assert has_blocking_open_issues(pl)
    assert open_issue_pages(pl) == [3]
    pl["open_issues"] = mark_issues_status(pl, status="skipped_by_user")
    assert not has_blocking_open_issues(pl)


def test_merge_keeps_prior_batch():
    pl = {
        "open_issues": [
            {"page": 5, "kind": "formula", "status": "open", "batch": "1-20"}
        ]
    }
    merged = merge_open_issues(
        pl, [{"page": 25, "kind": "layout", "status": "open", "batch": "21-40"}]
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


def test_no_reimport_after_skip():
    pl: dict = {"page_count": 178}
    note_imported_through(pl, 20)
    set_gate_batch(pl, 1, 20)
    assert imported_through(pl) == 20
    assert pl["segment_next_page"] == 1
    nxt = advance_past_gate_batch(pl)
    assert nxt == 21
    assert pl["segment_next_page"] == 21
    assert "segment_gate_end" not in pl
    # 已导入到 20，下一批从 21 起，不会再碰 1–20
    batches = segment_batches(178)
    resume = pl["segment_next_page"]
    start_idx = next(i for i, (a, b) in enumerate(batches) if a <= resume <= b or a >= resume)
    assert batches[start_idx] == (21, 40)


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
        batch="1-20",
    )
    assert open_issue_pages(pl) == [12]
