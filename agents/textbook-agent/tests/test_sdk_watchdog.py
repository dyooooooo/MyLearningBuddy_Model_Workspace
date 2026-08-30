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
        def resume(cid: str, opts: object = None) -> "Agent":
            raise RuntimeError("no resume")

        def send(self, *_a: object, **_k: object) -> Run:
            if send is not None:
                return send(*_a, **_k)
            return Run()

    mod.Agent = Agent
    mod.AgentOptions = AgentOptions
    mod.LocalAgentOptions = LocalAgentOptions
    mod.SendOptions = SendOptions
    monkeypatch.setitem(sys.modules, "cursor_sdk", mod)
    return flags


def _patch_runner(monkeypatch, tmp_path) -> list[int]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    recovered: list[int] = []
    monkeypatch.setattr(settings, "agent_home", str(home))
    monkeypatch.setattr(settings, "cursor_api_key", "test-key")
    monkeypatch.setattr(settings, "cursor_stall_timeout_s", 2.0)
    monkeypatch.setattr(settings, "cursor_send_timeout_s", 6.0)
    monkeypatch.setattr(settings, "cursor_eval_timeout_s", 8.0)
    monkeypatch.setattr(sdk_runner, "prepare_sdk_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(sdk_runner, "recover_bridge", lambda *_a, **_k: recovered.append(1))
    monkeypatch.setattr(sdk_runner, "custom_tools_for_sdk", lambda mode="agent": {})
    monkeypatch.setattr(sdk_runner, "mcp_servers_for_sdk", lambda: {})
    monkeypatch.setattr(sdk_runner, "model_selection", lambda model, params=None: model or "default")
    jobs.reset_for_tests()
    return recovered


def _wait_job(job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = jobs.get_job(job_id)
        if row and row["status"] in ("completed", "failed", "cancelled"):
            return row
        time.sleep(0.05)
    return jobs.get_job(job_id) or {}


def test_stream_silence_is_not_bridge_timeout(tmp_path, monkeypatch) -> None:
    recovered = _patch_runner(monkeypatch, tmp_path)

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
    time.sleep(3.2)
    mid = jobs.get_job(row["job_id"]) or {}
    assert mid.get("status") == "running"
    assert "桥接超时" not in (mid.get("error") or "")
    assert recovered == []
    jobs.request_cancel(row["job_id"])
    got = _wait_job(row["job_id"], timeout=4)
    assert got["status"] == "cancelled"


def test_stream_silence_then_tool_succeeds(tmp_path, monkeypatch) -> None:
    recovered = _patch_runner(monkeypatch, tmp_path)

    def stream():
        time.sleep(2.4)
        yield SimpleNamespace(type="tool_call", name="Edit", status="completed", args={"path": "a.py"})

    _install_fake_sdk(monkeypatch, stream=stream)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "hi"})

    def fn() -> dict:
        reply, _aid = sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
        return {"reply": reply}

    jobs.start_background(row["job_id"], fn)
    got = _wait_job(row["job_id"], timeout=8)
    assert got["status"] == "completed"
    assert recovered == []
    titles = [s.get("title") for s in got.get("trace") or []]
    assert any(str(t).startswith("Edit") for t in titles)


def test_eval_timeout_is_not_bridge_and_skips_recover(tmp_path, monkeypatch) -> None:
    recovered = _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "cursor_eval_timeout_s", 3.0)

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
    err = got.get("error") or ""
    assert "任务超时" in err
    assert "桥接超时" not in err
    assert recovered == []


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
    assert float(Settings.model_fields["cursor_send_timeout_s"].default) == 180.0
    assert float(Settings.model_fields["cursor_eval_timeout_s"].default) == 1800.0


def test_slow_send_past_stall_succeeds_without_recover(tmp_path, monkeypatch) -> None:
    recovered = _patch_runner(monkeypatch, tmp_path)

    def send(*_a: object, **_k: object):
        time.sleep(3.2)
        return SimpleNamespace(
            messages=lambda: iter(()),
            wait=lambda: SimpleNamespace(status="finished", result="ok", agent_id="a1"),
            cancel=lambda: None,
        )

    _install_fake_sdk(monkeypatch, send=send)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "hi"})

    def fn() -> dict:
        reply, _aid = sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
        return {"reply": reply}

    jobs.start_background(row["job_id"], fn)
    got = _wait_job(row["job_id"], timeout=8)
    assert got["status"] == "completed"
    assert recovered == []


def test_send_timeout_skips_recover_and_is_not_bridge(tmp_path, monkeypatch) -> None:
    recovered = _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "cursor_send_timeout_s", 2.0)

    def send(*_a: object, **_k: object):
        time.sleep(60)
        raise AssertionError("send should be abandoned by the watcher")

    _install_fake_sdk(monkeypatch, send=send)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "hi"})

    def fn() -> dict:
        sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
        return {"reply": "x"}

    jobs.start_background(row["job_id"], fn)
    got = _wait_job(row["job_id"], timeout=8)
    assert got["status"] == "failed"
    err = got.get("error") or ""
    assert "发送超时" in err
    assert "桥接超时" not in err
    assert recovered == []


def test_resume_sends_followup_and_binds_before_finish(tmp_path, monkeypatch) -> None:
    from app import conversations

    _patch_runner(monkeypatch, tmp_path)
    sent: list[object] = []
    resumed_opts: list[object] = []

    class LiveAgent:
        agent_id = "sdk-live"

        @staticmethod
        def create(_opts: object) -> "LiveAgent":
            raise AssertionError("should resume, not create")

        @staticmethod
        def resume(cid: str, opts: object = None) -> "LiveAgent":
            assert cid == "sdk-live"
            resumed_opts.append(opts)
            return LiveAgent()

        def send(self, payload: object, *_a: object, **_k: object):
            sent.append(payload)

            class Run:
                id = "run-1"

                def messages(self):
                    return iter(())

                def wait(self):
                    return SimpleNamespace(status="finished", result="ok", agent_id="sdk-live")

                def cancel(self) -> None:
                    return None

            return Run()

    import types

    mod = types.ModuleType("cursor_sdk")
    mod.Agent = LiveAgent
    mod.AgentOptions = type("AgentOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    mod.LocalAgentOptions = type("LocalAgentOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    mod.SendOptions = type("SendOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    monkeypatch.setitem(sys.modules, "cursor_sdk", mod)
    conversations.reset_for_tests()
    conversations._loaded_home = str(settings.home)
    conversations._convs["conv-1"] = {
        "id": "conv-1",
        "title": "t",
        "sdk_id": "sdk-live",
        "alias_ids": ["sdk-live"],
        "job_ids": [],
        "created_at": "2026-08-24T02:00:00+00:00",
        "updated_at": "2026-08-24T02:00:00+00:00",
    }
    jobs.reset_for_tests()
    jobs._loaded_home = str(settings.home)
    row = jobs.create_job("chat", {"message": "继续", "conversation_id": "conv-1"})
    reply, agent_id = sdk_runner._prompt_sdk(
        "系统前缀\n用户：\n继续",
        conversation_id="conv-1",
        resume_id="sdk-live",
        job_id=row["job_id"],
        followup="【断点续传】从这里接着做",
    )
    assert reply == "ok"
    assert agent_id == "sdk-live"
    assert sent == ["【断点续传】从这里接着做"]
    assert resumed_opts and resumed_opts[0] is not None
    assert conversations.resume_id("conv-1") == "sdk-live"
    mid = jobs.get_job(row["job_id"]) or {}
    assert (mid.get("result") or {}).get("agent_id") == "sdk-live"


def test_attaches_active_run_instead_of_sending(tmp_path, monkeypatch) -> None:
    from app import conversations

    _patch_runner(monkeypatch, tmp_path)
    cancelled: list[str] = []

    class LiveRun:
        id = "run-a"
        run_id = "run-a"
        _event_stream = object()

        def messages(self):
            return iter(())

        def wait(self):
            return SimpleNamespace(status="finished", result="from-A", agent_id="sdk-live")

        def cancel(self) -> None:
            raise AssertionError("should attach, not cancel")

    class LiveAgent:
        agent_id = "sdk-live"

        @staticmethod
        def create(_opts: object) -> "LiveAgent":
            raise AssertionError("should resume")

        @staticmethod
        def resume(cid: str, opts: object = None) -> "LiveAgent":
            assert cid == "sdk-live"
            return LiveAgent()

        @staticmethod
        def list_runs(aid: str, *_a: object, **_k: object):
            assert aid == "sdk-live"
            return [SimpleNamespace(id="run-a", status="running")]

        @staticmethod
        def get_run(run_id: str, options: object = None) -> LiveRun:
            assert run_id == "run-a"
            return LiveRun()

        @staticmethod
        def cancel_run(run_id: str, agent_id: str | None = None) -> None:
            cancelled.append(run_id)

        def send(self, *_a: object, **_k: object):
            raise AssertionError("should attach leftover run, not send")

    import types

    mod = types.ModuleType("cursor_sdk")
    mod.Agent = LiveAgent
    mod.AgentOptions = type("AgentOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    mod.LocalAgentOptions = type("LocalAgentOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    mod.SendOptions = type("SendOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    monkeypatch.setitem(sys.modules, "cursor_sdk", mod)
    conversations.reset_for_tests()
    conversations._loaded_home = str(settings.home)
    conversations._convs["conv-1"] = {
        "id": "conv-1",
        "title": "t",
        "sdk_id": "sdk-live",
        "alias_ids": ["sdk-live"],
        "job_ids": [],
        "created_at": "2026-08-25T02:00:00+00:00",
        "updated_at": "2026-08-25T02:00:00+00:00",
    }
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "继续", "conversation_id": "conv-1"})
    reply, agent_id = sdk_runner._prompt_sdk(
        "hi",
        conversation_id="conv-1",
        resume_id="sdk-live",
        job_id=row["job_id"],
        followup="【断点续传】",
    )
    assert reply == "from-A"
    assert agent_id == "sdk-live"
    assert cancelled == []
    titles = [s.get("title") for s in (jobs.get_job(row["job_id"]) or {}).get("trace") or []]
    assert "attach" in titles


def test_disposes_detached_leftover_then_sends(tmp_path, monkeypatch) -> None:
    _patch_runner(monkeypatch, tmp_path)
    cancelled: list[str] = []

    class DeadRun:
        id = "run-a"
        run_id = "run-a"

        def messages(self):
            raise RuntimeError("Cannot stream this run because no run event store is configured")

        def wait(self):
            raise RuntimeError("internal: Cannot wait on a detached running run")

        def cancel(self) -> None:
            cancelled.append("handle")

    class FreshRun:
        id = "run-new"
        _event_stream = object()

        def messages(self):
            return iter(())

        def wait(self):
            return SimpleNamespace(status="finished", result="fresh", agent_id="a1")

        def cancel(self) -> None:
            return None

    class LiveAgent:
        agent_id = "a1"
        leftover = [SimpleNamespace(id="run-a", status="running")]

        @staticmethod
        def create(_opts: object) -> "LiveAgent":
            return LiveAgent()

        @staticmethod
        def resume(*_a: object, **_k: object) -> "LiveAgent":
            raise RuntimeError("no resume")

        @staticmethod
        def list_runs(aid: str, *_a: object, **_k: object):
            return list(LiveAgent.leftover)

        @staticmethod
        def get_run(run_id: str, options: object = None) -> DeadRun:
            return DeadRun()

        @staticmethod
        def cancel_run(run_id: str, agent_id: str | None = None) -> None:
            cancelled.append(run_id)
            LiveAgent.leftover = []

        def send(self, *_a: object, **_k: object):
            if LiveAgent.leftover:
                raise RuntimeError("internal: Agent a1 already has active run")

            return FreshRun()

    import types

    mod = types.ModuleType("cursor_sdk")
    mod.Agent = LiveAgent
    mod.AgentOptions = type("AgentOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    mod.LocalAgentOptions = type("LocalAgentOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    mod.SendOptions = type("SendOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    monkeypatch.setitem(sys.modules, "cursor_sdk", mod)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "继续"})
    reply, agent_id = sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
    assert reply == "fresh"
    assert agent_id == "a1"
    assert "run-a" in cancelled
    titles = [s.get("title") for s in (jobs.get_job(row["job_id"]) or {}).get("trace") or []]
    assert "sweep" in titles


def test_detached_wait_then_force_send(tmp_path, monkeypatch) -> None:
    _patch_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(sdk_runner, "_wait_until_idle", lambda *_a, **_k: None)
    sends: list[object] = []

    class DetachedRun:
        id = "run-a"
        _event_stream = object()

        def messages(self):
            return iter(())

        def wait(self):
            raise RuntimeError("internal: Cannot wait on a detached running run")

        def cancel(self) -> None:
            return None

    class FreshRun:
        id = "run-new"
        _event_stream = object()

        def messages(self):
            return iter(())

        def wait(self):
            return SimpleNamespace(status="finished", result="forced", agent_id="a1")

        def cancel(self) -> None:
            return None

    class LiveAgent:
        agent_id = "a1"

        @staticmethod
        def create(_opts: object) -> "LiveAgent":
            return LiveAgent()

        @staticmethod
        def resume(*_a: object, **_k: object) -> "LiveAgent":
            raise RuntimeError("no resume")

        @staticmethod
        def list_runs(aid: str, *_a: object, **_k: object):
            return [SimpleNamespace(id="run-a", status="running")]

        @staticmethod
        def get_run(run_id: str, options: object = None) -> DetachedRun:
            return DetachedRun()

        @staticmethod
        def cancel_run(run_id: str, agent_id: str | None = None) -> None:
            return None

        def send(self, *_a: object, **_k: object):
            sends.append(_k.get("options") or (_a[1] if len(_a) > 1 else None))
            return FreshRun()

    import types

    mod = types.ModuleType("cursor_sdk")
    mod.Agent = LiveAgent
    mod.AgentOptions = type("AgentOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    mod.LocalAgentOptions = type("LocalAgentOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    mod.SendOptions = type("SendOptions", (), {"__init__": lambda self, **kw: setattr(self, "kw", kw)})
    monkeypatch.setitem(sys.modules, "cursor_sdk", mod)
    jobs.reset_for_tests()
    row = jobs.create_job("chat", {"message": "继续"})
    reply, agent_id = sdk_runner._prompt_sdk("hi", job_id=row["job_id"])
    assert reply == "forced"
    assert agent_id == "a1"
    assert sends
