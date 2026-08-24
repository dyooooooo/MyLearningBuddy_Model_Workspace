from __future__ import annotations

import sys
import time
import types
from types import SimpleNamespace

from app import jobs, sdk_runner
from app.config import settings


class ReadTimeout(Exception):
    pass


def _install_fake_sdk(
    monkeypatch,
    *,
    create=None,
    send=None,
    stream=None,
    wait=None,
    cancelled=None,
) -> list[bool]:
    flags = cancelled if cancelled is not None else []
    mod = types.ModuleType("cursor_sdk")

    class AgentOptions:
        def __init__(self, **kw: object) -> None:
            self.kw = kw

    class LocalAgentOptions:
        def __init__(self, **kw: object) -> None:
            self.kw = kw

    class SendOptions:
        def __init__(self, **kw: object) -> None:
            self.kw = kw

    class Run:
        def messages(self):
            if stream is None:
                return iter(())
            yield from stream()

        def wait(self):
            if wait is not None:
                return wait()
            return SimpleNamespace(status="finished", result="ok", agent_id="a1")

        def cancel(self) -> None:
            flags.append(True)

    class Agent:
        agent_id = "a1"

        @staticmethod
        def create(opts: object) -> "Agent":
            if create is not None:
                return create(opts)
            return Agent()

        @staticmethod
        def resume(cid: str) -> "Agent":
            raise RuntimeError("no resume")

        def send(self, *_a: object, **_k: object) -> Run:
            if send is not None:
                return send()
            return Run()

    mod.Agent = Agent
    mod.AgentOptions = AgentOptions
    mod.LocalAgentOptions = LocalAgentOptions
    mod.SendOptions = SendOptions
    monkeypatch.setitem(sys.modules, "cursor_sdk", mod)
    return flags


def _patch_runner(monkeypatch, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "agent_home", str(home))
    monkeypatch.setattr(settings, "cursor_api_key", "test-key")
    monkeypatch.setattr(settings, "cursor_stall_timeout_s", 2.0)
    monkeypatch.setattr(settings, "cursor_eval_timeout_s", 8.0)
    monkeypatch.setattr(sdk_runner, "prepare_sdk_turn", lambda: None)
    monkeypatch.setattr(sdk_runner, "recover_bridge", lambda: None)
    monkeypatch.setattr(sdk_runner, "custom_tools_for_sdk", lambda mode="agent": {})
    monkeypatch.setattr(sdk_runner, "mcp_servers_for_sdk", lambda: {})
    monkeypatch.setattr(sdk_runner, "model_selection", lambda model, params=None: model or "default")
    jobs.reset_for_tests()


def _wait_job(job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = jobs.get_job(job_id)
        if row and row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    return jobs.get_job(job_id) or {}


def test_stream_stall_fails_job_with_chinese(tmp_path, monkeypatch) -> None:
    _patch_runner(monkeypatch, tmp_path)

    def stream():
        while True:
            time.sleep(60)
            yield None

    _install_fake_sdk(monkeypatch, stream=stream)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "hi"})

    def fn() -> dict:
        sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
        return {"reply": "x"}

    jobs.start_background(row["job_id"], fn)
    got = _wait_job(row["job_id"], timeout=8)
    assert got["status"] == "failed"
    assert "桥接超时" in (got.get("error") or "")


def test_create_readtimeout_chinese_and_trace(tmp_path, monkeypatch) -> None:
    _patch_runner(monkeypatch, tmp_path)
    n = {"create": 0}

    def create(_opts: object):
        n["create"] += 1
        raise ReadTimeout("Bridge request timed out: ReadTimeout: timed out")

    _install_fake_sdk(monkeypatch, create=create)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "hi"})

    def fn() -> dict:
        sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
        return {"reply": "x"}

    jobs.start_background(row["job_id"], fn)
    got = _wait_job(row["job_id"], timeout=6)
    assert got["status"] == "failed"
    assert "桥接超时" in (got.get("error") or "")
    assert got.get("trace")
    assert n["create"] == 2


def test_cancel_wins_over_stall(tmp_path, monkeypatch) -> None:
    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "cursor_stall_timeout_s", 8.0)

    def stream():
        while True:
            time.sleep(60)
            yield None

    flags = _install_fake_sdk(monkeypatch, stream=stream)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "hi"})

    def fn() -> dict:
        sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
        return {"reply": "x"}

    jobs.start_background(row["job_id"], fn)
    for _ in range(50):
        if (jobs.get_job(row["job_id"]) or {}).get("status") == "running":
            break
        time.sleep(0.05)
    jobs.request_cancel(row["job_id"])
    got = _wait_job(row["job_id"], timeout=4)
    assert got["status"] == "cancelled"
    assert "桥接超时" not in (got.get("error") or "")
    _ = flags


def test_hook_deny_fails_immediately(tmp_path, monkeypatch) -> None:
    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "cursor_stall_timeout_s", 8.0)

    def stream():
        yield SimpleNamespace(
            type="tool_call",
            name="Read",
            status="error",
            permission="deny",
            user_message="不能读 .cursor/projects",
            args={},
        )
        while True:
            time.sleep(60)
            yield None

    _install_fake_sdk(monkeypatch, stream=stream)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "hi"})

    def fn() -> dict:
        sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
        return {"reply": "x"}

    jobs.start_background(row["job_id"], fn)
    got = _wait_job(row["job_id"], timeout=4)
    assert got["status"] == "failed"
    assert "权限拒绝" in (got.get("error") or "")


def test_default_stall_is_45s() -> None:
    from app.config import Settings

    assert float(Settings.model_fields["cursor_stall_timeout_s"].default) == 45.0
    assert float(Settings.model_fields["cursor_eval_timeout_s"].default) == 300.0
