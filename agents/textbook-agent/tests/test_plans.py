from __future__ import annotations

from app.config import settings
from app import plans
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    ensure_workspace()
    return home


def test_capture_writes_reply_when_no_new_file(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    before = plans.snapshot_index()
    row = plans.capture_after_plan("# 整理目录\n\n1. 读 PDF\n", before, "job-abc123")
    assert row["title"] == "整理目录"
    assert "读 PDF" in row["markdown"]
    path = settings.home / row["path"]
    assert path.is_file()
    assert "整理目录" in path.read_text(encoding="utf-8")


def test_capture_picks_new_plan_file(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    before = plans.snapshot_index()
    dest = home / ".cursor" / "plans" / "from-sdk.md"
    dest.write_text("# SDK 计划\n\n- 第一步\n", encoding="utf-8")
    row = plans.capture_after_plan("会被文件盖掉", before, "job-1")
    assert row["id"] == "from-sdk"
    assert row["title"] == "SDK 计划"
    assert "第一步" in row["markdown"]


def test_title_from_markdown() -> None:
    assert plans.title_from_markdown("# Hello\n\nbody") == "Hello"
    assert plans.title_from_markdown("plain first\nmore") == "plain first"


def test_finalize_adds_checkpoint(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    before = plans.snapshot_index()
    row = plans.finalize("# 整理目录\n\n1. 读 PDF\n", before, "job-fin")
    assert row["checkpoint"]
    assert (home / row["path"]).is_file()


def test_finalize_uses_create_plan_trace(tmp_path, monkeypatch) -> None:
    import json

    _home(tmp_path, monkeypatch)
    before = plans.snapshot_index()
    trace = [
        {
            "kind": "tool",
            "title": "createPlan completed",
            "detail": json.dumps({"plan": "# TOC 优化\n\n1. 停造小节\n"}, ensure_ascii=False),
        }
    ]
    row = plans.finalize("已写好计划，请确认。", before, "job-cp", trace=trace)
    assert row["title"] == "TOC 优化"
    assert "停造小节" in row["markdown"]


def test_markdown_from_truncated_create_plan() -> None:
    import json

    body = "# TOC 优化：印刷目录为权威\n\n" + ("步骤说明。" * 80)
    raw = json.dumps({"plan": body}, ensure_ascii=False)[:2000]
    md = plans.markdown_from_trace([{"title": "createPlan completed", "detail": raw}])
    assert md.startswith("# TOC 优化")
    assert len(md) > 80


def test_hydrate_old_plan_job(tmp_path, monkeypatch) -> None:
    import json

    home = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(home)
    row = jobs.create_job("chat", {"message": "整理", "mode": "plan"})
    jobs._set(
        row["job_id"],
        persist=True,
        status="completed",
        result={"mode": "plan", "reply": "已写好计划", "checkpoint": "abc"},
        trace=[
            {
                "title": "createPlan completed",
                "detail": json.dumps({"plan": "# 清洗目录\n\n1. 读印刷目录\n"}, ensure_ascii=False),
            }
        ],
    )
    live = jobs.get_job(row["job_id"])
    assert live is not None
    assert plans.hydrate(live)
    assert live["result"]["plan"]["title"] == "清洗目录"
    assert live["result"]["plan"]["checkpoint"] == "abc"
    again = jobs.get_job(row["job_id"])
    assert again is not None
    assert again["result"]["plan"]["title"] == "清洗目录"
    assert plans.hydrate(again) is False
