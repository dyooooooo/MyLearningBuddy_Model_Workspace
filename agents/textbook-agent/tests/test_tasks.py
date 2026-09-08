from __future__ import annotations

import time

from app import bus, conversations, jobs, library, refine, sdk_runner, tasks
from app.config import settings
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    settings.agent_home = str(home)
    jobs.reset_for_tests()
    tasks.reset_for_tests()
    conversations.reset_for_tests()
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
    refine = next(
        job
        for job in started
        if str(((job.get("payload") or {}) if isinstance(job.get("payload"), dict) else {}).get("task_id") or "")
        == "import-page-refine"
    )
    assert all(
        str(((job.get("payload") or {}) if isinstance(job.get("payload"), dict) else {}).get("task_id") or "")
        != "import-book-toc"
        for job in started
    )
    row = _wait(refine["job_id"])
    assert row["status"] == "completed"
    assert row["kind"] == "chat"
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    assert "b1" in str((payload or {}).get("message") or "")
    assert str((payload or {}).get("trigger") or "") == "kb.import.completed"
    assert str((payload or {}).get("task_id") or "") == "import-page-refine"
    assert str((payload or {}).get("model") or "") == "grok-4.6"
    assert (payload or {}).get("model_params") == [
        {"id": "effort", "value": "high"},
        {"id": "fast", "value": "true"},
    ]
    assert (row.get("result") or {}).get("model") == "grok-4.6"
    assert "start_layout_refine" not in str((payload or {}).get("message") or "")


def test_import_book_toc_starts_printed_job(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b2", label="生物")
    library.write_page("b2", 1, "目录\n第1章 遗传 1\n")
    library.write_page("b2", 2, "第1章 遗传\n")
    library.patch_book("b2", status="ready", page_count=2, subject="biology", category="textbook")

    conv = conversations.ensure("", "先判断科目")
    cid = str(conv["id"])
    library.patch_book("b2", conversation_id=cid)
    started = tasks.emit("task.import-page-refine.completed", {"book_id": "b2", "conversation_id": cid})
    toc_jobs = [
        job
        for job in started
        if str(((job.get("payload") or {}) if isinstance(job.get("payload"), dict) else {}).get("task_id") or "")
        == "import-book-toc"
    ]
    assert toc_jobs
    assert all(job.get("kind") == "printed_toc" for job in toc_jobs)
    assert all(job.get("kind") != "chat" or (job.get("payload") or {}).get("task_id") != "import-book-toc" for job in started)

    row = _wait(toc_jobs[0]["job_id"], timeout=8.0)
    assert row["status"] == "completed"
    assert row["kind"] == "printed_toc"
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    assert str((payload or {}).get("trigger") or "") == "task.import-page-refine.completed"
    cid = str((payload or {}).get("conversation_id") or "")
    assert cid == str(conv["id"])
    live = conversations.get(cid)
    assert live is not None
    assert "建目录" not in str(live.get("title") or "")
    assert str(row["job_id"]) in [str(x) for x in (live.get("job_ids") or [])]

    write = _wait_task_job("write-toc-pages", timeout=4.0)
    assert write["kind"] == "chat"
    write_payload = write.get("payload") if isinstance(write.get("payload"), dict) else {}
    assert str((write_payload or {}).get("conversation_id") or "") == cid
    write_row = _wait(write["job_id"])
    assert write_row["status"] == "completed"


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
                        "id": "import-book-toc",
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
    row = tasks.get_task("import-book-toc")
    assert row is not None
    assert row.get("action") == "printed_toc"
    assert "toc_extract" in (row.get("message") or "")
    assert "resolve_printed_toc" in (row.get("message") or "")
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
    # 跑过一轮之后 message 仍要跟 builtin.yaml 走，不能被 overlay 冻住
    assert "refine_book" in (again.get("message") or "")
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
    for job in started:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
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
    assert "kb.import.converting" in ids
    assert "task.import-page-refine.completed" in ids
    assert "task.import-book-toc.completed" in ids
    assert "task.chinese-textbook-layout.completed" in ids
    assert "tool.write_toc_pages" in ids
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
    refine = next(
        job
        for job in started
        if str(((job.get("payload") or {}) if isinstance(job.get("payload"), dict) else {}).get("task_id") or "")
        == "import-page-refine"
    )
    payload = refine.get("payload") if isinstance(refine.get("payload"), dict) else {}
    assert str((payload or {}).get("task_id") or "") == "import-page-refine"
    assert "start_layout_refine" not in str((payload or {}).get("message") or "")
    assert "build_toc" not in str((payload or {}).get("message") or "")
    row = _wait(refine["job_id"])
    assert row["status"] == "completed"
    time.sleep(0.25)
    for item in jobs.list_jobs(80):
        extra = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        assert str((extra or {}).get("task_id") or "") != "layout-after-clean"
        assert str((extra or {}).get("task_id") or "") != "chinese-textbook-layout"


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


def test_schedule_drops_condition(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    row = tasks.create_task(
        "夜间",
        mode="schedule",
        cron="0 3 * * *",
        message="夜间检查",
        condition="这本书是高中数学",
    )
    assert row["condition"] == ""
    assert "条件不满足" not in tasks._render_message(row)


def test_trigger_empty_condition_runs_plain(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    row = tasks.create_task("入库", mode="trigger", message="导入后清洗 {{book_id}}")
    assert row["condition"] == ""
    job = tasks.start_task(row, {"book_id": "b1"})
    assert job is not None
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    assert str((payload or {}).get("message") or "") == "导入后清洗 b1"


def test_trigger_condition_wraps_agent_message(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    row = tasks.create_task(
        "入库",
        mode="trigger",
        message="请清洗 {{book_id}}",
        condition="这本书是高中数学",
    )
    assert row["condition"] == "这本书是高中数学"
    job = tasks.start_task(row, {"book_id": "b1"})
    assert job is not None
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    text = str((payload or {}).get("message") or "")
    assert "这本书是高中数学" in text
    assert "请清洗 b1" in text
    assert tasks.CONDITION_SKIP in text
    assert text.count("条件：这本书是高中数学") == 1
    assert text.count(tasks.CONDITION_SKIP) == 1


def test_builtin_condition_overlay_persists(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    updated = tasks.update_task("import-page-refine", condition="仅高中数学教材")
    assert updated is not None
    assert updated["condition"] == "仅高中数学教材"
    tasks.reset_for_tests()
    again = tasks.get_task("import-page-refine")
    assert again is not None
    assert again["condition"] == "仅高中数学教材"
    # 只覆盖了 condition，message 仍应取 builtin.yaml 的正文
    assert "refine_book" in (again.get("message") or "")


def test_switch_to_schedule_clears_condition(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    row = tasks.create_task(
        "入库",
        mode="trigger",
        message="清洗",
        condition="这本书是高中数学",
    )
    switched = tasks.update_task(row["id"], mode="schedule", cron="0 3 * * *")
    assert switched is not None
    assert switched["mode"] == "schedule"
    assert switched["condition"] == ""


def test_create_task_tool_accepts_condition(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app.tools_loader import load_tool_specs

    specs = {s["id"]: s for s in load_tool_specs()}
    created = specs["create_task"]["execute"](
        {
            "title": "有条件",
            "mode": "trigger",
            "trigger": "kb.import.completed",
            "message": "清洗",
            "condition": "科目是数学",
        }
    )
    assert created["condition"] == "科目是数学"
    specs["update_task"]["execute"]({"task_id": created["id"], "condition": "科目是语文"})
    assert tasks.get_task(created["id"])["condition"] == "科目是语文"


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


def test_when_gates_chemistry_layout(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="c1", label="化学")
    library.patch_book("c1", status="ready", subject="math", category="textbook")
    row = tasks.get_task("chemistry-textbook-layout")
    assert row is not None
    assert row["when"]["subject"] == "chemistry"
    assert not (row.get("condition") or "").strip()
    wash = (tasks.get_task("import-page-refine") or {}).get("message") or ""
    assert "只调用一次 refine_book" in wash
    assert tasks.start_task(row, {"book_id": "c1"}) is None
    library.patch_book("c1", subject="chemistry", category="textbook")
    job = tasks.start_task(row, {"book_id": "c1"})
    assert job is not None


def test_when_gates_chinese_layout(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b1", label="演示")
    library.patch_book("b1", status="ready", subject="math", category="textbook")
    row = tasks.get_task("chinese-textbook-layout")
    assert row is not None
    assert row["when"]["subject"] == "chinese"
    assert tasks.start_task(row, {"book_id": "b1"}) is None
    library.patch_book("b1", subject="chinese", category="textbook")
    job = tasks.start_task(row, {"book_id": "b1"})
    assert job is not None


def test_when_accepts_category_list(tmp_path, monkeypatch) -> None:
    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="s1", label="课标")
    meta = library.read_meta("s1")
    meta["category"] = "考纲"
    meta["subject"] = "math"
    library.write_meta("s1", meta)
    row = tasks.get_task("write-toc-pages-syllabus")
    assert row is not None
    job = tasks.start_task(row, {"book_id": "s1"})
    assert job is not None
    assert (home / "textbooks" / "s1" / "meta.json").is_file()


def test_create_task_tool_accepts_when(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    from app.tools_loader import load_tool_specs

    specs = {s["id"]: s for s in load_tool_specs()}
    created = specs["create_task"]["execute"](
        {
            "title": "带 when",
            "mode": "trigger",
            "trigger": "kb.import.completed",
            "message": "清洗",
            "when": '{"subject":"math","category":["textbook","syllabus"]}',
        }
    )
    assert created["when"]["subject"] == "math"
    assert created["when"]["category"] == ["textbook", "syllabus"]
    specs["update_task"]["execute"]({"task_id": created["id"], "when": '{"subject":"chinese"}'})
    assert tasks.get_task(created["id"])["when"]["subject"] == "chinese"


def test_tool_skipped_status_is_emitted(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    seen: list[tuple] = []
    orig = tasks.emit

    def wrapped(event, payload=None):
        seen.append((event, dict(payload or {})))
        return orig(event, payload)

    monkeypatch.setattr(tasks, "emit", wrapped)
    from app.tools_loader import _wrap

    fn = _wrap(lambda book_id="": {"skipped": True, "reason": "no_shape"}, "fill_unit_toc")
    fn({"book_id": "b1"})
    hit = next(item for item in seen if item[0] == "tool.fill_unit_toc")
    assert hit[1]["status"] == "skipped"
    assert hit[1]["skipped"] is True


def test_convert_progress_stays_on_same_conversation(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_CONVERT_POLL_S", 0.01)
    monkeypatch.setattr(tasks, "_CONVERT_HEARTBEAT_S", 30.0)
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="c1", label="生物")
    library.patch_book("c1", status="converting")
    conv = conversations.ensure("", "先判断科目")
    cid = str(conv["id"])
    library.patch_book("c1", conversation_id=cid)

    started = tasks.emit("kb.import.converting", {"book_id": "c1", "conversation_id": cid})
    assert started
    job = started[0]
    assert job["kind"] == "convert"
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    assert str((payload or {}).get("conversation_id") or "") == cid
    assert "开始识别全书" in str((payload or {}).get("message") or "")
    live = conversations.get(cid)
    assert str(job["job_id"]) in [str(x) for x in (live.get("job_ids") or [])]
    assert "建目录" not in str(live.get("title") or "")

    library.write_page("c1", 1, "正文\n")
    library.patch_book("c1", status="ready", page_count=1)
    row = _wait(job["job_id"], timeout=3.0)
    assert row["status"] == "completed"
    assert (row.get("result") or {}).get("page_count") == 1
    assert "全书识别完成" in str((row.get("result") or {}).get("reply") or "")


def test_convert_progress_needs_existing_conversation(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="c2", label="生物")
    library.patch_book("c2", status="converting")
    assert tasks.emit("kb.import.converting", {"book_id": "c2"}) == []


def test_printed_toc_uses_book_conversation(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "_prompt_sdk", lambda *a, **k: ("ok", "sdk"))
    library.create_book(pdf_bytes=b"%PDF", filename="demo.pdf", book_id="b3", label="生物")
    library.write_page("b3", 1, "目录\n第1章 遗传 1\n")
    library.patch_book("b3", status="ready", page_count=1, subject="biology", category="textbook")
    conv = conversations.ensure("", "先判断")
    cid = str(conv["id"])
    library.patch_book("b3", conversation_id=cid)
    started = tasks.emit("task.import-page-refine.completed", {"book_id": "b3"})
    toc = next(
        job
        for job in started
        if str(((job.get("payload") or {}) if isinstance(job.get("payload"), dict) else {}).get("task_id") or "")
        == "import-book-toc"
    )
    payload = toc.get("payload") if isinstance(toc.get("payload"), dict) else {}
    assert str((payload or {}).get("conversation_id") or "") == cid
