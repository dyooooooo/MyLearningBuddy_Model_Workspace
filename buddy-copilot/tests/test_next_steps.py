"""状态化下一步按钮与意图。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.next_steps import (  # noqa: E402
    build_next_step_actions,
    looks_like_finalize_request,
    looks_like_refuse_principle,
    next_steps_message,
)
from app.segment_state import append_layout_change, has_layout_or_effective_ledger  # noqa: E402


def test_actions_when_open_issues():
    pl = {
        "open_issues": [{"page": 3, "kind": "layout", "status": "open"}],
        "import_job_id": "j1",
        "segment_imported_through": 20,
    }
    acts = build_next_step_actions(pl)
    ids = {a["id"] for a in acts}
    assert "accept" in ids
    assert "skip" in ids
    assert len(acts) >= 1
    msg = next_steps_message(pl)
    assert msg["actions"]
    assert "待办" in msg["text"] or "3" in msg["text"]


def test_actions_finalize_when_ledger():
    pl = {
        "import_job_id": "j1",
        "page_count": 40,
        "segment_next_page": 41,
        "segment_imported_through": 40,
        "layout_change_log": [{"id": "a", "op": "patch_block"}],
    }
    acts = build_next_step_actions(pl)
    assert any(a["id"] == "finalize" for a in acts)


def test_actions_after_result_edition():
    pl = {"result_edition_id": "ed_ai_20260725"}
    acts = build_next_step_actions(pl)
    assert acts[0]["id"] == "open_edition"


def test_layout_change_log_append():
    pl: dict = {}
    pl["layout_change_log"] = append_layout_change(
        pl, {"op": "replace_page_blocks", "page": 2, "summary": "reorder"}
    )
    assert has_layout_or_effective_ledger(pl)
    assert pl["layout_change_log"][0]["op"] == "replace_page_blocks"
    assert pl["layout_change_log"][0]["id"]


def test_finalize_and_refuse_intent():
    assert looks_like_finalize_request("生成新排版方案")
    assert looks_like_refuse_principle("请改架构并跳过权限")
    assert not looks_like_refuse_principle("按建议改第 3 页")
