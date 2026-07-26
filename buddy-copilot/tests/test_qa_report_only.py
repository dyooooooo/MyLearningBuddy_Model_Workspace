"""质检只看最终 HTML、只列问题；导入 wait 按批就绪；用户分流修/不修。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.segment_state import (  # noqa: E402
    fix_instruction,
    import_batch_ready,
    mentioned_open_pages,
    note_qa_through,
    parse_page_ranges,
    parse_user_fix_pages,
    qa_instruction,
    qa_through,
    user_wants_skip_and_continue,
)
from app.next_steps import build_next_step_actions  # noqa: E402


def test_qa_instruction_is_report_only():
    text = qa_instruction(
        job_id="j1",
        start=1,
        end=20,
        page_count=178,
        run_index=1,
        run_total=9,
        open_pages=[],
        edition_id="e1",
    )
    assert "page_html" in text
    assert "只报告" in text or "不修改" in text
    assert "禁止改页" in text
    assert "get_blocks" in text and "不得调用" in text
    assert "中间结构化" in text or "结构化块" in text
    assert "fixed_pages" in text


def test_fix_instruction_scoped_pages():
    text = fix_instruction(job_id="j1", pages=[3, 10], edition_id="e1")
    assert "3" in text and "10" in text
    assert "get_blocks" in text
    assert "禁止改用户未点名" in text or "未点名" in text


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


def test_next_steps_buttons_for_issues():
    actions = build_next_step_actions(
        {
            "open_issues": [{"page": 3, "kind": "layout", "status": "open"}],
            "import_job_id": "j1",
            "segment_imported_through": 20,
        }
    )
    labels = {a["label"] for a in actions}
    sends = {a["send"] for a in actions}
    assert "不修，下一批" in labels
    assert "不修，下一批" in sends
    assert "按建议改" in sends
