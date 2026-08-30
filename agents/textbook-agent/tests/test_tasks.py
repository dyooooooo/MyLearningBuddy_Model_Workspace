from __future__ import annotations

import time

from app import bus, jobs, library, refine, sdk_runner, tasks
from app.config import settings
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    jobs.reset_for_tests()
    tasks.reset_for_tests()
    bus.reset_for_tests()
    ensure_workspace()
    return home


def _wait(job_id: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = jobs.get_job(job_id)
        if row and row.get("status") in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def test_builtin_trigger_starts_agent_chat(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk-refine"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b1", label="演示")
    library.write_page("b1", 1, "正文\n")
    library.patch_book("b1", status="ready", page_count=1)

    builtin = next(t for t in tasks.list_tasks() if t["id"] == "import-page-refine")
    assert builtin["mode"] == "trigger"
    assert builtin["trigger"] == "kb.import.completed"
    assert "{{book_id}}" in (builtin.get("message") or "")
    assert "propose_import_meta" in (builtin.get("identify_message") or "")
    assert "第1册" in (builtin.get("identify_message") or "")
    assert "MinerU" in (builtin.get("identify_message") or "")
    assert "open_pdf_page" in (builtin.get("identify_message") or "")
    assert "{{preview}}" in (builtin.get("identify_message") or "")
    assert (home / "prompts" / "page_refine.md").is_file()
    assert all(t["id"] != "layout-after-clean" for t in tasks.list_tasks())
    assert "start_layout_refine" not in (builtin.get("message") or "")

    started = tasks.emit("kb.import.completed", {"book_id": "b1"})
    assert started
    row = _wait(started[0]["job_id"])
    assert row["status"] == "completed"
    assert row["kind"] == "chat"
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    assert "b1" in str((payload or {}).get("message") or "")
    assert str((payload or {}).get("trigger") or "") == "kb.import.completed"
    assert str((payload or {}).get("task_id") or "") == "import-page-refine"
    assert str((payload or {}).get("model") or "") == "grok-4.6"
    assert (payload or {}).get("model_params") == [
        {"id": "effort", "value": "xhigh"},
        {"id": "fast", "value": "true"},
    ]
    assert (row.get("result") or {}).get("model") == "grok-4.6"
    assert "start_layout_refine" not in str((payload or {}).get("message") or "")


def test_schedule_persists_next_run(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    row = tasks.create_task("夜间", mode="schedule", cron="0 3 * * *", message="夜间检查")
    assert row["next_run"]
    first = row["next_run"]
    tasks.reset_for_tests()
    again = tasks.get_task(row["id"])
    assert again is not None
    assert again["next_run"] == first


def test_manual_mode_rejected(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    try:
        tasks.create_task("人手", mode="manual")
        raise AssertionError("expected manual mode to fail")
    except ValueError as exc:
        assert "schedule or trigger" in str(exc)


def test_oneshot_schedule_disables_after_tick(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timedelta, timezone

    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    row = tasks.create_task("一次", mode="schedule", next_run=due, message="跑一次检查")
    assert row["cron"] == ""
    assert row["next_run"]
    started = tasks.tick()
    assert started
    assert started[0]["kind"] == "chat"
    again = tasks.get_task(row["id"])
    assert again is not None
    assert again["enabled"] is False
    assert again["next_run"] == ""


def test_create_list_cancel_tools(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app.tools_loader import load_tool_specs

    specs = {s["id"]: s for s in load_tool_specs()}
    assert "create_task" in specs
    assert "list_tasks" in specs
    assert "cancel_task" in specs
    assert "refine_page" in specs
    created = specs["create_task"]["execute"](
        {"title": "导入洗", "mode": "trigger", "trigger": "kb.import.completed", "message": "书 {{book_id}} 导入后清洗"}
    )
    listed = specs["list_tasks"]["execute"]({})
    ids = [t["id"] for t in listed["tasks"]]
    assert created["id"] in ids
    specs["cancel_task"]["execute"]({"task_id": created["id"]})
    assert tasks.get_task(created["id"])["enabled"] is False


def test_trigger_allows_catalog_events(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    row = tasks.create_task("入库", mode="trigger", message="导入后清洗")
    assert row["trigger"] == "kb.import.completed"
    ok = tasks.create_task("目录后", mode="trigger", trigger="tool.build_toc", message="目录已建")
    assert ok["trigger"] == "tool.build_toc"
    dropped = tasks.create_task("认书", mode="trigger", trigger="kb.import.dropped", message="认前三页")
    assert dropped["trigger"] == "kb.import.dropped"
    try:
        tasks.create_task("其它", mode="trigger", trigger="other.event", message="x")
        raise AssertionError("expected unknown trigger to fail")
    except ValueError as exc:
        assert "unknown trigger" in str(exc)


def test_update_and_view_detail(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    row = tasks.create_task("夜间", mode="schedule", cron="0 3 * * *", message="夜间检查")
    updated = tasks.update_task(row["id"], title="凌晨清洗", cron="15 4 * * *", message="凌晨检查默认教材")
    assert updated is not None
    assert updated["title"] == "凌晨清洗"
    assert updated["cron"] == "15 4 * * *"
    assert updated["message"] == "凌晨检查默认教材"
    tasks.reset_for_tests()
    again = tasks.get_task_detail(row["id"])
    assert again is not None
    assert again["title"] == "凌晨清洗"
    assert again["cron"] == "15 4 * * *"
    assert "凌晨检查" in (again.get("message") or again.get("prompt_text") or "")
    builtin = tasks.update_task("import-page-refine", enabled=False)
    assert builtin is not None
    tasks.reset_for_tests()
    assert tasks.get_task("import-page-refine")["enabled"] is False


def test_builtin_message_ignores_stale_overlay(tmp_path, monkeypatch) -> None:
    import json

    home = _home(tmp_path, monkeypatch)
    (home / "tasks" / "tasks.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "import-page-refine",
                        "message": "旧文案只有 build_toc，没有抽子目录",
                        "title": "旧标题",
                        "enabled": True,
                        "last_run_at": "2026-01-01T00:00:00+00:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tasks.reset_for_tests()
    row = tasks.get_task("import-page-refine")
    assert row is not None
    assert "toc_extract" in (row.get("message") or "")
    assert row["title"] != "旧标题"
    assert row["last_run_at"] == "2026-01-01T00:00:00+00:00"

    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b1", label="演示")
    library.write_page("b1", 1, "正文\n")
    library.patch_book("b1", status="ready", page_count=1)
    started = tasks.emit("kb.import.completed", {"book_id": "b1"})
    assert started
    tasks.reset_for_tests()
    again = tasks.get_task("import-page-refine")
    assert again is not None
    assert "toc_extract" in (again.get("message") or "")
    stored = json.loads((home / "tasks" / "tasks.json").read_text(encoding="utf-8"))
    overlay = next(item for item in stored["tasks"] if item["id"] == "import-page-refine")
    assert "message" not in overlay


def test_refine_cursor_survives_reset(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b2", label="二")
    library.write_page("b2", 1, "第一页\n")
    refine.save_cursor("b2", {"next_page": 7, "sdk_id": "keep-me"})
    tasks.reset_for_tests()
    cursor = refine.load_cursor("b2")
    assert cursor["next_page"] == 7
    assert cursor["sdk_id"] == "keep-me"


def test_emit_reuses_conversation(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b1", label="演示")
    library.write_page("b1", 1, "正文\n")
    library.patch_book("b1", status="ready", page_count=1)
    from app import conversations

    conv = conversations.ensure("", "先判断")
    cid = str(conv["id"])
    started = tasks.emit("kb.import.completed", {"book_id": "b1", "conversation_id": cid})
    assert started
    payload = started[0].get("payload") if isinstance(started[0].get("payload"), dict) else {}
    assert str((payload or {}).get("conversation_id") or "") == cid


def _wait_task_job(task_id: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for job in jobs.list_jobs(80):
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if str((payload or {}).get("task_id") or "") == task_id:
                return job
        time.sleep(0.05)
    raise AssertionError(f"job for task {task_id} did not start")


def test_list_events_catalog(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    events = tasks.list_events()
    ids = {e["id"] for e in events}
    assert "kb.import.completed" in ids
    assert "kb.import.dropped" in ids
    assert "task.import-page-refine.completed" in ids
    assert "task.layout-after-clean.completed" not in ids
    assert "tool.refine_page" in ids
    assert "tool.build_toc" in ids
    assert "tool.create_task" not in ids
    refine = next(e for e in events if e["id"] == "task.import-page-refine.completed")
    assert refine["group"] == "任务完成"
    assert "清洗" in refine["label"]


def test_clean_complete_does_not_start_layout(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b1", label="演示")
    library.write_page("b1", 1, "正文\n")
    library.patch_book("b1", status="ready", page_count=1)
    from app import conversations

    conv = conversations.ensure("", "先判断")
    cid = str(conv["id"])
    started = tasks.emit("kb.import.completed", {"book_id": "b1", "conversation_id": cid})
    assert started
    first = started[0]
    payload = first.get("payload") if isinstance(first.get("payload"), dict) else {}
    assert str((payload or {}).get("task_id") or "") == "import-page-refine"
    assert "start_layout_refine" not in str((payload or {}).get("message") or "")
    row = _wait(first["job_id"])
    assert row["status"] == "completed"
    time.sleep(0.25)
    for item in jobs.list_jobs(80):
        extra = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        assert str((extra or {}).get("task_id") or "") != "layout-after-clean"


def test_identify_chat_does_not_start_layout(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b1", label="演示")
    library.write_page("b1", 1, "正文\n")
    library.patch_book("b1", status="ready", page_count=1)
    from app import conversations

    conv = conversations.ensure("", "认书")
    cid = str(conv["id"])
    task = tasks.get_task("import-page-refine")
    assert task is not None
    job = tasks.start_chat_task(
        task,
        {"book_id": "b1", "conversation_id": cid, "trigger": "kb.import.dropped", "message": "认科目"},
    )
    row = _wait(job["job_id"])
    assert row["status"] == "completed"
    time.sleep(0.25)
    for item in jobs.list_jobs(80):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        assert str((payload or {}).get("task_id") or "") != "layout-after-clean"


def test_tool_success_can_start_task(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    tasks.create_task("目录后", mode="trigger", trigger="tool.build_toc", message="目录已建。书号 {{book_id}}。")
    from app.tools_loader import _wrap

    fn = _wrap(lambda book_id="": "ok", "build_toc")
    fn({"book_id": "b1"})
    hit = None
    deadline = time.time() + 3.0
    while time.time() < deadline:
        for job in jobs.list_jobs(80):
            payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
            if "目录已建" in str((payload or {}).get("message") or ""):
                hit = job
                break
        if hit:
            break
        time.sleep(0.05)
    assert hit is not None
    payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else {}
    assert str((payload or {}).get("book_id") or "") == "b1"
    assert str((payload or {}).get("trigger") or "") == "tool.build_toc"


def test_emit_reentrancy_skips_same_event(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b1", label="演示")
    library.write_page("b1", 1, "正文\n")
    library.patch_book("b1", status="ready", page_count=1)
    nested: list[list] = []
    orig = tasks.start_task

    def wrapped(task, extra=None):
        nested.append(tasks.emit(str(task.get("trigger") or ""), extra or {}))
        return orig(task, extra)

    monkeypatch.setattr(tasks, "start_task", wrapped)
    started = tasks.emit("kb.import.completed", {"book_id": "b1"})
    assert started
    assert nested
    assert nested[0] == []
