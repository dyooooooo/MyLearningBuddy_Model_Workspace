from __future__ import annotations

from app.resume_context import compose_prompts, format_brief, is_continue, original_task


def test_is_continue_phrases() -> None:
    assert is_continue("继续")
    assert is_continue("请继续")
    assert is_continue("再试一次")
    assert is_continue("我现在已经升级了你的权限，你再试一次")
    assert not is_continue("把三级目录补进导入")
    assert not is_continue("")


def test_original_task_skips_continue_nudges() -> None:
    jobs = [
        _job("j1", "completed", "把三级目录补进导入"),
        _job("j2", "failed", "我现在已经升级了你的权限，你再试一次", error="Cursor 桥接超时，云端或本地桥没有回包"),
        _job("j3", "queued", "继续"),
    ]
    assert original_task(jobs, 1) == "把三级目录补进导入"


def test_format_brief_pins_last_turn_not_older_chats() -> None:
    job = _job(
        "j2",
        "failed",
        "把三级目录补进导入",
        error="Cursor 桥接超时，云端或本地桥没有回包",
        trace=[
            {"kind": "tool", "title": "connect", "detail": "正在连接 Cursor"},
            {"kind": "tool", "title": "read completed", "detail": '{"path": "tools/build_toc.py"}'},
            {"kind": "think", "title": "思考", "detail": "先改 build_toc，不要动 /app。"},
        ],
    )
    brief = format_brief(job, user_now="继续")
    assert "【断点续传】" in brief
    assert "把三级目录补进导入" in brief
    assert "桥接超时" in brief
    assert "tools/build_toc.py" in brief
    assert "不要打开 traces/" in brief
    assert "agent-transcripts" in brief
    assert "第十页" not in brief


def test_compose_prompts_continue_uses_previous_failed_job(tmp_path, monkeypatch) -> None:
    from app import conversations, jobs
    from app.config import settings

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(settings, "agent_home", str(home))
    jobs.reset_for_tests()
    conversations.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conversations._loaded_home = str(settings.home)
    cid = "conv-1"
    jobs._jobs["old"] = _job(
        "old",
        "failed",
        "把三级目录补进导入",
        error="Cursor 桥接超时，云端或本地桥没有回包",
        cid=cid,
        trace=[{"kind": "tool", "title": "read completed", "detail": '{"path": "tools/build_toc.py"}'}],
    )
    jobs._jobs["now"] = _job("now", "running", "继续", cid=cid)
    conversations._convs[cid] = {
        "id": cid,
        "title": "t",
        "sdk_id": "",
        "alias_ids": [],
        "job_ids": ["old", "now"],
        "created_at": "2026-08-24T02:00:00+00:00",
        "updated_at": "2026-08-24T02:01:00+00:00",
    }
    full, followup, meta = compose_prompts(
        "继续",
        system="系统前缀\n用户：\n",
        conversation_id=cid,
        job_id="now",
    )
    assert meta["has_breakpoint"] is True
    assert "把三级目录补进导入" in followup
    assert "tools/build_toc.py" in followup
    assert followup.startswith("【断点续传】")
    assert full.startswith("系统前缀")
    assert "第十页界面优化" not in followup


def test_compose_prompts_injects_working_note_on_new_message(tmp_path, monkeypatch) -> None:
    from app import conversations, jobs, working_memory
    from app.config import settings

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(settings, "agent_home", str(home))
    jobs.reset_for_tests()
    conversations.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conversations._loaded_home = str(settings.home)
    cid = "conv-note"
    dest = working_memory.working_path(cid)
    assert dest is not None
    dest.write_text("# 工作笔记\n用户任务：把三级目录补进导入\n停住原因：桥接超时\n", encoding="utf-8")
    (settings.memory_dir / "DECISIONS.md").write_text("目录必须带印张页\n", encoding="utf-8")
    full, followup, meta = compose_prompts(
        "下一本也按同样规则",
        system="系统前缀\n用户：\n",
        conversation_id=cid,
        job_id="now",
    )
    assert meta["has_breakpoint"] is False
    assert followup.startswith("【工作笔记】")
    assert "把三级目录补进导入" in followup
    assert "【跨对话结论】" in followup
    assert "目录必须带印张页" in followup
    assert "下一本也按同样规则" in followup
    assert "【断点续传】" not in followup
    assert full.startswith("系统前缀")


def test_compose_prompts_continue_keeps_brief_after_notes(tmp_path, monkeypatch) -> None:
    from app import conversations, jobs, working_memory
    from app.config import settings

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(settings, "agent_home", str(home))
    jobs.reset_for_tests()
    conversations.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conversations._loaded_home = str(settings.home)
    cid = "conv-1"
    jobs._jobs["old"] = _job(
        "old",
        "failed",
        "把三级目录补进导入",
        error="Cursor 桥接超时，云端或本地桥没有回包",
        cid=cid,
        trace=[{"kind": "tool", "title": "read completed", "detail": '{"path": "tools/build_toc.py"}'}],
    )
    jobs._jobs["now"] = _job("now", "running", "继续", cid=cid)
    conversations._convs[cid] = {
        "id": cid,
        "title": "t",
        "sdk_id": "",
        "alias_ids": [],
        "job_ids": ["old", "now"],
        "created_at": "2026-08-24T02:00:00+00:00",
        "updated_at": "2026-08-24T02:01:00+00:00",
    }
    dest = working_memory.working_path(cid)
    assert dest is not None
    dest.write_text("# 工作笔记\n用户任务：把三级目录补进导入\n", encoding="utf-8")
    _full, followup, meta = compose_prompts(
        "继续",
        system="系统前缀\n用户：\n",
        conversation_id=cid,
        job_id="now",
    )
    assert meta["has_breakpoint"] is True
    assert followup.startswith("【工作笔记】")
    assert "【断点续传】" in followup
    assert followup.index("【工作笔记】") < followup.index("【断点续传】")
    assert "tools/build_toc.py" in followup


def _job(
    job_id: str,
    status: str,
    message: str,
    *,
    error: str = "",
    cid: str = "",
    trace: list | None = None,
) -> dict:
    return {
        "job_id": job_id,
        "kind": "chat",
        "status": status,
        "payload": {"message": message, "conversation_id": cid},
        "result": {"conversation_id": cid} if cid else {},
        "error": error,
        "trace": list(trace or []),
        "created_at": "2026-08-24T02:00:00+00:00",
        "updated_at": "2026-08-24T02:00:10+00:00",
    }
