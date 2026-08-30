from __future__ import annotations

import time

from app import conversations, jobs, sdk_runner
from app.auto_resume import kick_once, should_auto_resume
from app.config import settings
from app.sdk_errors import BUSY_ZH, DETACHED_ZH, STALL_ZH, WAIT_ZH


def _home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(settings, "agent_home", str(home))
    jobs.reset_for_tests()
    conversations.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    conversations._loaded_home = str(settings.home)


def _job(
    job_id: str,
    status: str,
    message: str,
    *,
    error: str = "",
    cid: str = "conv-1",
    extra: dict | None = None,
) -> dict:
    payload = {"message": message, "conversation_id": cid, "mode": "agent"}
    if extra:
        payload.update(extra)
    return {
        "job_id": job_id,
        "kind": "chat",
        "status": status,
        "payload": payload,
        "result": {"conversation_id": cid},
        "error": error,
        "trace": [],
        "created_at": "2026-08-25T01:00:00+00:00",
        "updated_at": "2026-08-25T01:00:10+00:00",
    }


def test_should_auto_resume_only_first_connect_stall() -> None:
    ok = _job("j1", "failed", "补全第五章标题", error=STALL_ZH)
    assert should_auto_resume(ok)
    assert should_auto_resume(_job("j2b", "failed", "继续", error=BUSY_ZH))
    assert should_auto_resume(_job("j2d", "failed", "补全第五章标题", error=DETACHED_ZH))
    assert not should_auto_resume(_job("j2", "failed", "继续", error=STALL_ZH))
    assert not should_auto_resume(
        _job("j3", "failed", "补全第五章标题", error=STALL_ZH, extra={"auto_resume": True})
    )
    assert not should_auto_resume(_job("j4", "failed", "补全第五章标题", error=WAIT_ZH))
    assert not should_auto_resume(_job("j5", "failed", "补全第五章标题", error="权限拒绝：不能读"))
    assert not should_auto_resume(_job("j6", "cancelled", "补全第五章标题", error=STALL_ZH))
    no_cid = _job("j7", "failed", "补全第五章标题", error=STALL_ZH, cid="")
    no_cid["payload"]["conversation_id"] = ""
    no_cid["result"] = {}
    assert not should_auto_resume(no_cid)


def test_kick_once_then_stop(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sdk_runner, "recover_bridge", lambda *_a, **_k: None)
    calls: list[str] = []

    def fake_chat(message: str, conversation_id: str = "", **_k: object) -> dict:
        calls.append(message)
        raise TimeoutError(STALL_ZH)

    monkeypatch.setattr(sdk_runner, "run_chat", fake_chat)
    conv = conversations.ensure("", "补全第五章标题")
    cid = str(conv["id"])
    row = jobs.create_job("chat", {"message": "补全第五章标题", "conversation_id": cid, "mode": "agent"})
    conversations.attach_job(cid, row["job_id"])

    def boom() -> dict:
        raise TimeoutError(STALL_ZH)

    jobs.start_background(row["job_id"], boom)
    deadline = time.time() + 4
    nxt = ""
    while time.time() < deadline:
        first = jobs.get_job(row["job_id"]) or {}
        nxt = str((first.get("result") or {}).get("auto_resume_job_id") or "")
        second = jobs.get_job(nxt) if nxt else None
        if first.get("status") == "failed" and second and second.get("status") == "failed":
            break
        time.sleep(0.05)
    first = jobs.get_job(row["job_id"]) or {}
    nxt = str((first.get("result") or {}).get("auto_resume_job_id") or "")
    second = jobs.get_job(nxt) or {}
    assert first["status"] == "failed"
    assert nxt
    assert second.get("payload", {}).get("auto_resume") is True
    assert second.get("payload", {}).get("message") == "继续"
    assert second["status"] == "failed"
    assert calls == ["继续"]
    assert len(conversations.conversation_jobs(cid)) == 2
    assert kick_once(nxt) == ""
    assert len(conversations.conversation_jobs(cid)) == 2
