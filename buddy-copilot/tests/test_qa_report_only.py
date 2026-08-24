"""全书自动改指令含 escalate；导入 wait 按页就绪；用户分流修/不修。"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.segment_state import (  # noqa: E402
    fix_instruction,
    fullbook_analyze_and_fix_instruction,
    import_batch_ready,
    mentioned_open_pages,
    note_qa_through,
    parse_page_ranges,
    parse_user_fix_pages,
    qa_through,
    user_wants_skip_and_continue,
)
from app.next_steps import build_next_step_actions  # noqa: E402


def test_fullbook_instruction_has_escalate_and_autofix():
    text = fullbook_analyze_and_fix_instruction(
        job_id="j1",
        page_count=178,
        edition_id="e1",
        escalate_pages=[3, 10, 42],
        escalate_summary={"low_keys": ["band_main"], "page_3": {"score": 0.2}},
        open_pages=[],
    )
    assert "layout_escalate_pages" in text
    assert "3" in text and "10" in text
    assert "不要等用户" in text or "勿等" in text or "不要停下来" in text
    assert "禁止" in text and "35B" in text
    assert "rebuild_ir" in text
    assert "needs_user" in text


def test_fix_instruction_scoped_pages():
    text = fix_instruction(job_id="j1", pages=[3, 10], edition_id="e1")
    assert "3" in text and "10" in text
    assert "get_blocks" in text
    assert "rebuild_ir" in text


def test_import_batch_ready_not_fooled_by_partial_pages():
    job = {"status": "uploaded", "meta": {"pages_ready": [1]}}
    assert not import_batch_ready(job, page_ranges="1-20")
    assert not import_batch_ready(job, page_limit=20)

    job2 = {"status": "chunked", "meta": {"pages_ready": [1]}}
    assert import_batch_ready(job2, page_ranges="1-20")  # 终态即可

    ready = {"status": "parsing", "meta": {"pages_ready": list(range(1, 21))}}
    assert import_batch_ready(ready, page_ranges="1-20")

    mid = {"status": "parsing", "meta": {"pages_ready": list(range(21, 41))}}
    assert import_batch_ready(mid, page_ranges="21-40")
    assert not import_batch_ready(mid, page_ranges="1-20")


def test_parse_page_ranges():
    assert parse_page_ranges("1-3") == [1, 2, 3]
    assert parse_page_ranges("21-40")[0] == 21
    assert parse_page_ranges("3,5,7-8") == [3, 5, 7, 8]


def test_mentioned_and_parse_fix_pages():
    open_pages = [3, 10, 15]
    assert mentioned_open_pages("修第 3、10 页", open_pages) == [3, 10]
    assert mentioned_open_pages("按建议改", open_pages) == []
    assert parse_user_fix_pages("按建议改", open_pages) == [3, 10, 15]
    assert parse_user_fix_pages("只改第 15 页", open_pages) == [15]


def test_skip_intent_includes_no_fix_next_batch():
    assert user_wants_skip_and_continue("不修，下一批")
    assert user_wants_skip_and_continue("导入下一批")
    assert user_wants_skip_and_continue("不用改")


def test_qa_through_tracks_completed_batches():
    pl: dict = {}
    assert qa_through(pl) == 0
    note_qa_through(pl, 20)
    assert qa_through(pl) == 20
    note_qa_through(pl, 40)
    assert qa_through(pl) == 40
    note_qa_through(pl, 30)  # 不回退
    assert qa_through(pl) == 40


def test_next_steps_buttons_no_next_batch_gate():
    actions = build_next_step_actions(
        {
            "open_issues": [{"page": 3, "kind": "layout", "status": "open"}],
            "import_job_id": "j1",
            "segment_imported_through": 178,
            "page_count": 178,
        }
    )
    labels = {a["label"] for a in actions}
    sends = {a["send"] for a in actions}
    assert "不修，下一批" not in labels
    assert "导入下一批" not in labels
    assert "按建议改" in sends
    assert "结束" in labels


def test_fullbook_start_import_omits_page_ranges():
    """Orchestrator must call start_pdf_import without page_ranges / page_limit."""
    from app.tools.buddy_api import BuddyApiClient  # noqa: E402

    client = BuddyApiClient.__new__(BuddyApiClient)
    captured: dict = {}

    def fake_req(method, path, *, json=None, params=None):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        return {"id": "job-full"}

    client._req = fake_req  # type: ignore[method-assign]
    out = BuddyApiClient.start_pdf_import(
        client,
        minio_key="staged/x.pdf",
        edition_id="e1",
        label="book",
    )
    assert out["id"] == "job-full"
    assert "page_ranges" not in (captured.get("json") or {})
    assert "page_limit" not in (captured.get("json") or {})


def test_get_blocks_exposes_layout_provenance():
    from app.tools.plugins import corpus_tools as ct  # noqa: E402
    from app.tools.registry import ToolContext  # noqa: E402

    client = MagicMock()
    client.get_job_blocks.return_value = {
        "blocks": [
            {
                "id": "b1",
                "type": "text",
                "page_no": 3,
                "order": 1,
                "text": "hello",
                "meta": {
                    "layout_zone": "aside_right",
                    "layout_rule_id": "hujiao.tip",
                    "layout_confidence": "low",
                    "layout_confidence_score": 0.3,
                    "layout_evidence": {"right_of_main": True},
                    "layout_escalate": True,
                },
            }
        ]
    }
    ctx = MagicMock(spec=ToolContext)
    ctx.session_id = "s1"
    ctx.kind = "import_advice"
    ctx.store = MagicMock()
    ctx.client = client
    specs = ct.build(ctx)
    get_blocks = next(s.fn for s in specs if s.name == "corpus.get_blocks")
    out = get_blocks("j1", 3)
    assert out["blocks"][0]["layout_rule_id"] == "hujiao.tip"
    assert out["blocks"][0]["layout_confidence_score"] == 0.3
    assert out["blocks"][0]["layout_evidence"]["right_of_main"] is True
