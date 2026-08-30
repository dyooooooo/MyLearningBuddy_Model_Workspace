from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.workspace import ensure_workspace


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    from app.config import settings
    from app import conversations

    settings.agent_home = str(tmp_path / "home")
    settings.textbook_agent_token = "test-token"
    conversations.reset_for_tests()
    ensure_workspace()
    return settings, conversations


def test_title_from_first_message() -> None:
    from app.conversations import clean_agent_title, make_title

    assert make_title("帮我把目录标题修好") == "把目录标题修好"
    assert make_title("") == "新对话"
    assert clean_agent_title("「修目录显示」\n其他") == "修目录显示"
    assert clean_agent_title("") == ""


def test_chained_cursor_ids_merge_into_one_thread(tmp_path, monkeypatch) -> None:
    _settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs._jobs.clear()
    jobs._loaded_home = str(_settings.home)
    chain = [
        ("j1", "", "A"),
        ("j2", "A", "B"),
        ("j3", "B", "C"),
        ("j4", "C", "D"),
    ]
    for i, (jid, pay, res) in enumerate(chain):
        jobs._jobs[jid] = {
            "job_id": jid,
            "kind": "chat",
            "status": "completed",
            "payload": {"message": f"第{i + 1}句", "conversation_id": pay},
            "result": {"conversation_id": res, "reply": "ok"},
            "error": "",
            "trace": [],
            "created_at": f"2026-08-20T08:0{i}:00+00:00",
            "updated_at": f"2026-08-20T08:0{i}:10+00:00",
        }
    monkeypatch.setattr("app.sdk_runner.summarize_title", lambda _blob: "修目录并清理轨迹")
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    rows = client.get("/v1/conversations", headers=headers).json()["conversations"]
    assert len(rows) == 1
    assert rows[0]["turns"] == 4
    detail = client.get(f"/v1/conversations/{rows[0]['id']}", headers=headers).json()
    assert [j["job_id"] for j in detail["jobs"]] == ["j1", "j2", "j3", "j4"]
    conversations.set_title(rows[0]["id"], "修目录并清理轨迹", "agent")
    assert conversations.get("D")["id"] == rows[0]["id"]
    assert conversations.get(rows[0]["id"])["title"] == "修目录并清理轨迹"


def _seed_thread(jobs, conversations, cid_seed: str, message: str, stamp: str) -> str:
    conv = conversations.ensure("", message=message)
    row = jobs.create_job("chat", {"message": message, "conversation_id": conv["id"]})
    jid = row["job_id"]
    jobs._jobs[jid]["status"] = "completed"
    jobs._jobs[jid]["created_at"] = stamp
    jobs._jobs[jid]["updated_at"] = stamp
    conversations.attach_job(conv["id"], jid)
    return str(conv["id"])


def test_reattaching_a_job_does_not_restamp_the_thread(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conv = conversations.ensure("", message="只问一句")
    row = jobs.create_job("chat", {"message": "只问一句", "conversation_id": conv["id"]})
    conversations.attach_job(conv["id"], row["job_id"])
    before = conversations.get(conv["id"])["updated_at"]
    conversations.attach_job(conv["id"], row["job_id"])
    assert conversations.get(conv["id"])["updated_at"] == before


def test_listing_keeps_the_newest_thread_first(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    old = _seed_thread(jobs, conversations, "old", "上周问的", "2026-08-20T08:00:00+00:00")
    mid = _seed_thread(jobs, conversations, "mid", "前天问的", "2026-08-25T08:00:00+00:00")
    new = _seed_thread(jobs, conversations, "new", "刚刚问的", "2026-08-28T08:00:00+00:00")
    # The backfill sweep re-attaches every known job on every listing.
    listed = conversations.list_conversations()
    listed = conversations.list_conversations()
    assert [row["id"] for row in listed] == [new, mid, old]
    assert conversations.get(old)["updated_at"] == "2026-08-20T08:00:00+00:00"
    assert conversations.get(new)["updated_at"] == "2026-08-28T08:00:00+00:00"


def test_rewind_deletes_turns_from_trajectory(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conv = conversations.ensure("", message="第一句")
    first = jobs.create_job("chat", {"message": "第一句", "conversation_id": conv["id"]})
    second = jobs.create_job("chat", {"message": "继续", "conversation_id": conv["id"]})
    jobs._jobs[first["job_id"]]["status"] = "completed"
    jobs._jobs[second["job_id"]]["status"] = "failed"
    conversations.attach_job(conv["id"], first["job_id"])
    conversations.attach_job(conv["id"], second["job_id"])
    out = conversations.rewind_to_job(second["job_id"])
    assert second["job_id"] in out["removed_job_ids"]
    assert jobs.get_job(second["job_id"]) is None
    assert (settings.traces_dir / f"{second['job_id']}.json").exists() is False
    listed = conversations.list_conversations()
    assert conv["id"] in {row["id"] for row in listed}
    assert not any((row.get("preview") or "") == "继续" for row in listed)


def test_list_drops_already_rewound_orphan_shells(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conversations.reset_for_tests()
    conversations._loaded_home = str(settings.home)
    ghost = jobs.create_job("chat", {"message": "继续", "conversation_id": "", "rewound": True})
    jobs._jobs[ghost["job_id"]]["status"] = "failed"
    jobs.detach_conversation(ghost["job_id"])
    conversations._convs["ghost-1"] = {
        "id": "ghost-1",
        "title": "继续",
        "title_source": "fallback",
        "sdk_id": "",
        "alias_ids": [],
        "job_ids": [ghost["job_id"]],
        "created_at": "2026-08-24T03:15:11+00:00",
        "updated_at": "2026-08-24T03:15:11+00:00",
    }
    listed = conversations.list_conversations()
    assert "ghost-1" not in {row["id"] for row in listed}
    assert jobs.get_job(ghost["job_id"]) is None


def test_same_conversation_stays_one_thread(tmp_path, monkeypatch) -> None:
    _settings, conversations = _home(tmp_path, monkeypatch)

    def fake_chat(message: str, conversation_id: str = "", job_id: str = "", **_kw) -> dict:
        return {"conversation_id": conversation_id, "reply": f"回：{message}", "book_missing": False}

    monkeypatch.setattr("app.sdk_runner.run_chat", fake_chat)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    first = client.post("/v1/chat", headers=headers, json={"message": "帮我看看第6页目录"})
    assert first.status_code == 202
    cid = first.json()["conversation_id"]
    title = first.json()["title"]
    assert cid
    assert title == "看看第6页目录"
    second = client.post(
        "/v1/chat",
        headers=headers,
        json={"message": "再改一下标题标签", "conversation_id": cid},
    )
    assert second.json()["conversation_id"] == cid
    listed = client.get("/v1/conversations", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()["conversations"]
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["turns"] == 2
    assert rows[0]["title"] == title
    detail = client.get(f"/v1/conversations/{cid}", headers=headers)
    assert detail.status_code == 200
    jobs = detail.json()["jobs"]
    assert len(jobs) == 2
    assert jobs[0]["payload"]["message"] == "帮我看看第6页目录"


def test_plan_checkpoint_keeps_plan_job(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conv = conversations.ensure("", message="写计划")
    plan_job = jobs.create_job(
        "chat",
        {"message": "写计划", "conversation_id": conv["id"], "mode": "plan"},
    )
    build_job = jobs.create_job(
        "chat",
        {
            "message": "按此执行",
            "conversation_id": conv["id"],
            "mode": "agent",
            "plan_id": "toc-fix",
        },
    )
    jobs._jobs[plan_job["job_id"]]["status"] = "completed"
    jobs._jobs[plan_job["job_id"]]["result"] = {
        "conversation_id": conv["id"],
        "plan": {"id": "toc-fix", "title": "整理目录", "checkpoint": "plan-sha-1"},
    }
    jobs._jobs[build_job["job_id"]]["status"] = "completed"
    conversations.attach_job(conv["id"], plan_job["job_id"])
    conversations.attach_job(conv["id"], build_job["job_id"])
    out = conversations.rewind_to_checkpoint("plan-sha-1")
    assert out["kept_plan"] is True
    assert plan_job["job_id"] in out["kept_job_ids"]
    assert build_job["job_id"] in out["removed_job_ids"]
    assert jobs.get_job(plan_job["job_id"]) is not None
    assert jobs.get_job(build_job["job_id"]) is None
    plan = (jobs.get_job(plan_job["job_id"]) or {}).get("result", {}).get("plan") or {}
    assert plan.get("rolled_back") is not True


def test_plan_checkpoint_without_build_marks_rolled_back(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conv = conversations.ensure("", message="写计划")
    plan_job = jobs.create_job(
        "chat",
        {"message": "写计划", "conversation_id": conv["id"], "mode": "plan"},
    )
    jobs._jobs[plan_job["job_id"]]["status"] = "completed"
    jobs._jobs[plan_job["job_id"]]["result"] = {
        "conversation_id": conv["id"],
        "plan": {"id": "toc-fix", "title": "整理目录", "checkpoint": "plan-sha-2"},
    }
    conversations.attach_job(conv["id"], plan_job["job_id"])
    out = conversations.rewind_to_checkpoint("plan-sha-2")
    assert out["kept_plan"] is True
    assert plan_job["job_id"] in out["kept_job_ids"]
    plan = (jobs.get_job(plan_job["job_id"]) or {}).get("result", {}).get("plan") or {}
    assert plan.get("rolled_back") is True


def test_new_chat_is_a_new_conversation(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)

    def fake_chat(message: str, conversation_id: str = "", **_kw) -> dict:
        return {"conversation_id": conversation_id, "reply": "ok", "book_missing": False}

    monkeypatch.setattr("app.sdk_runner.run_chat", fake_chat)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    a = client.post("/v1/chat", headers=headers, json={"message": "第一场"}).json()["conversation_id"]
    b = client.post("/v1/chat", headers=headers, json={"message": "第二场"}).json()["conversation_id"]
    assert a != b
    rows = client.get("/v1/conversations", headers=headers).json()["conversations"]
    assert len(rows) == 2


def _seed_job(jobs, jid: str, *, cid: str, agent_id: str = "", conversation_id: str = "", updated: str = "2026-08-27T08:00:00+00:00") -> None:
    jobs._jobs[jid] = {
        "job_id": jid,
        "kind": "chat",
        "status": "completed",
        "payload": {"message": "继续", "conversation_id": cid},
        "result": {
            "conversation_id": conversation_id or cid,
            "reply": "ok",
            **({"agent_id": agent_id} if agent_id else {}),
        },
        "error": "",
        "trace": [],
        "created_at": updated,
        "updated_at": updated,
    }


def test_ensure_with_client_id_does_not_store_it_as_sdk_id(tmp_path, monkeypatch) -> None:
    _settings, conversations = _home(tmp_path, monkeypatch)
    row = conversations.ensure("front-end-id", message="第一句")
    assert row["id"] == "front-end-id"
    assert row["sdk_id"] == ""
    assert conversations.resume_id("front-end-id") == ""


def test_merge_takes_sdk_id_from_agent_id_not_conversation_id(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    cid = "local-conv"
    conversations.ensure(cid, message="第一句")
    conversations.bind_sdk(cid, "agent-old")
    _seed_job(jobs, "j-new", cid=cid, agent_id="agent-new", updated="2026-08-27T09:00:00+00:00")
    _seed_job(jobs, "j-old", cid=cid, agent_id="agent-old", updated="2026-08-27T08:00:00+00:00")
    conversations.coalesce_linked_threads()
    assert conversations.get(cid)["sdk_id"] == "agent-new"


def test_merge_ignores_result_conversation_id(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    cid = "local-conv"
    conversations.ensure(cid, message="第一句")
    conversations.bind_sdk(cid, "agent-keep")
    _seed_job(jobs, "j1", cid=cid, conversation_id=cid, updated="2026-08-27T09:00:00+00:00")
    conversations.coalesce_linked_threads()
    assert conversations.get(cid)["sdk_id"] == "agent-keep"
    assert conversations.resume_id(cid) == "agent-keep"


def test_merge_does_not_pick_a_random_alias(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    cid = "local-conv"
    conversations.ensure(cid, message="第一句")
    conversations.bind_sdk(cid, "agent-keep")
    _seed_job(jobs, "j1", cid=cid, conversation_id="alias-a")
    _seed_job(jobs, "j2", cid=cid, conversation_id="alias-b")
    conversations.coalesce_linked_threads()
    assert conversations.get(cid)["sdk_id"] == "agent-keep"


def test_resume_id_heals_dirty_local_id(tmp_path, monkeypatch) -> None:
    _settings, conversations = _home(tmp_path, monkeypatch)
    cid = "local-conv"
    conversations.ensure(cid, message="第一句")
    conversations._convs[cid]["sdk_id"] = cid
    conversations._persist(conversations._convs[cid])
    assert conversations.resume_id(cid) == ""
    assert conversations.get(cid)["sdk_id"] == ""


def test_bind_sdk_rejects_a_local_conversation_id(tmp_path, monkeypatch) -> None:
    _settings, conversations = _home(tmp_path, monkeypatch)
    cid = "local-conv"
    conversations.ensure(cid, message="第一句")
    conversations.bind_sdk(cid, cid)
    assert conversations.get(cid)["sdk_id"] == ""
    conversations.bind_sdk(cid, "agent-real")
    assert conversations.get(cid)["sdk_id"] == "agent-real"


def test_completed_job_keeps_agent_id_and_resumed(tmp_path, monkeypatch) -> None:
    settings, _conversations = _home(tmp_path, monkeypatch)
    from app import jobs
    import time

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    row = jobs.create_job("chat", {"message": "hi", "conversation_id": "c1"})
    jobs.patch_result(row["job_id"], agent_id="agent-live", run_id="run-1", resumed=True)

    def fn() -> dict:
        return {"conversation_id": "c1", "reply": "ok"}

    jobs.start_background(row["job_id"], fn)
    for _ in range(40):
        live = jobs.get_job(row["job_id"]) or {}
        if live.get("status") == "completed":
            result = live.get("result") or {}
            assert result["agent_id"] == "agent-live"
            assert result["run_id"] == "run-1"
            assert result["resumed"] is True
            assert result["reply"] == "ok"
            return
        time.sleep(0.05)
    raise AssertionError("job did not complete")


def test_a_retired_cursor_thread_is_never_handed_back_for_resume(tmp_path, monkeypatch) -> None:
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs, transport_breaker

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    transport_breaker.reset()
    dead = "47848966-3f76-4db1-a989-539f5a8c1229"
    conv = conversations.ensure("", message="从 83 页开始还有乱码")
    cid = str(conv["id"])
    with conversations._lock:
        conversations._convs[cid]["sdk_id"] = dead
    assert conversations.resume_id(cid) == dead

    conversations.retire_sdk_id(dead)
    assert conversations.resume_id(cid) == ""
    assert dead in (conversations.get(cid).get("dead_sdk_ids") or [])


def test_the_retirement_outlives_a_restart(tmp_path, monkeypatch) -> None:
    """The breaker is in-memory, so only the on-disk record survives a rebuild."""
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import transport_breaker

    dead = "dead-thread-1"
    conv = conversations.ensure("", message="继续")
    cid = str(conv["id"])
    with conversations._lock:
        conversations._convs[cid]["sdk_id"] = dead
    conversations.retire_sdk_id(dead)

    transport_breaker.reset()
    conversations.reset_for_tests()
    settings.agent_home = str(tmp_path / "home")
    assert conversations.resume_id(cid) == ""


def test_a_retired_thread_is_not_re_derived_from_old_jobs(tmp_path, monkeypatch) -> None:
    """A rewind clears sdk_id, but the coalesce pass re-reads result.agent_id."""
    settings, conversations = _home(tmp_path, monkeypatch)
    from app import jobs

    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    dead = "dead-thread-2"
    conv = conversations.ensure("", message="从断点继续")
    cid = str(conv["id"])
    row = jobs.create_job("chat", {"message": "从断点继续", "conversation_id": cid})
    jid = row["job_id"]
    jobs._jobs[jid]["status"] = "failed"
    jobs._jobs[jid]["result"] = {"agent_id": dead, "resumed": True}
    conversations.attach_job(cid, jid)

    conversations.retire_sdk_id(dead)
    conversations.list_conversations()
    assert conversations.resume_id(cid) == ""
