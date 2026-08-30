from __future__ import annotations

from app.sdk_runner import WRITE_TOOL_IDS, _sdk_mode, tool_allowed
from app.config import settings
from app.workspace import ensure_workspace


def test_sdk_mode_maps_ask_and_plan_separately() -> None:
    assert _sdk_mode("ask") == "ask"
    assert _sdk_mode("plan") == "plan"
    assert _sdk_mode("agent") == "agent"
    assert _sdk_mode("CHAT") == "agent"


def test_ask_and_plan_drop_write_tools() -> None:
    assert not tool_allowed("ask", "write_tool")
    assert not tool_allowed("plan", "create_task")
    assert not tool_allowed("plan", "write_skill")
    assert tool_allowed("ask", "search_md")
    assert tool_allowed("plan", "open_pdf_page")
    assert tool_allowed("agent", "write_tool")
    for tool_id in WRITE_TOOL_IDS:
        assert tool_allowed("agent", tool_id)
        assert not tool_allowed("ask", tool_id)
        assert not tool_allowed("plan", tool_id)


def test_run_chat_plan_attaches_result(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    ensure_workspace()
    from app import conversations, jobs, sdk_runner

    conversations.reset_for_tests()
    jobs.reset_for_tests()
    jobs._loaded_home = str(home)
    conv = conversations.ensure("", "整理目录")
    row = jobs.create_job(
        "chat",
        {"message": "整理目录", "conversation_id": conv["id"], "mode": "plan"},
    )
    monkeypatch.setattr(
        sdk_runner,
        "_prompt_sdk",
        lambda *a, **k: ("# 整理目录\n\n1. 读 PDF\n", "agent-1"),
    )
    out = sdk_runner.run_chat("整理目录", conv["id"], job_id=row["job_id"], mode="plan")
    assert out["plan"]["title"] == "整理目录"
    assert out["plan"]["path"]
    assert out["plan"]["checkpoint"]
    assert (home / out["plan"]["path"]).is_file()
