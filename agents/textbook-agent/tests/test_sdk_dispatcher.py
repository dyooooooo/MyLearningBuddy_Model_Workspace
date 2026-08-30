"""Official one-client dispatcher: reuse handles, never tear a sibling stream."""

from __future__ import annotations

from types import SimpleNamespace

from app import conversations, cursor_auth, jobs, sdk_client, sdk_runner
from app.config import settings


class _FakeAgent:
    def __init__(self, aid: str) -> None:
        self.agent_id = aid
        self.closed = 0
        self.reloads = 0

    def close(self) -> None:
        self.closed += 1

    def reload(self) -> None:
        self.reloads += 1


def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "home"))
    settings.agent_home = str(tmp_path / "home")
    jobs.reset_for_tests()
    conversations.reset_for_tests()
    sdk_client.reset_for_tests()


def test_drop_dead_bridge_skips_when_sibling_running(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    closed: list[int] = []
    monkeypatch.setattr(sdk_client, "close_owned_client", lambda: closed.append(1))
    other = jobs.create_job("chat", {})
    jobs._set(other["job_id"], status="running")
    assert cursor_auth._drop_dead_bridge() is False
    assert closed == []


def test_drop_dead_bridge_closes_only_when_idle(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    closed: list[int] = []
    monkeypatch.setattr(sdk_client, "close_owned_client", lambda: closed.append(1))
    monkeypatch.setattr(sdk_client, "ensure_client", lambda: None)
    monkeypatch.setattr("app.bridge_cleanup.sweep_orphan_bridges", lambda **_k: None)
    assert cursor_auth._drop_dead_bridge() is True
    assert closed == [1]


def test_resume_and_create_receive_the_owned_client(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    client = object()
    sdk_client.install_client_for_tests(client)
    seen: list[tuple[str, object]] = []

    class AgentCls:
        @staticmethod
        def resume(cid, opts=None, *, client=None):
            seen.append(("resume", client))
            return _FakeAgent(cid)

        @staticmethod
        def create(opts, *, client=None):
            seen.append(("create", client))
            return _FakeAgent("new-agent")

    monkeypatch.setattr(sdk_runner, "_call_watched", lambda fn, **_k: fn())
    agent, resumed = sdk_runner._resume_or_create(
        AgentCls, "sdk-old", object(), "job-1", conversation_id="conv-1"
    )
    assert resumed is True
    assert agent.agent_id == "sdk-old"
    assert seen == [("resume", client)]
    assert sdk_client.peek_agent(conversation_id="conv-1") is agent

    sdk_client.reset_for_tests()
    sdk_client.install_client_for_tests(client)
    seen.clear()
    created, resumed = sdk_runner._resume_or_create(
        AgentCls, "", object(), "job-2", conversation_id="conv-2"
    )
    assert resumed is False
    assert created.agent_id == "new-agent"
    assert seen == [("create", client)]


def test_dispatcher_reuses_the_cached_handle(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    cached = _FakeAgent("sdk-1")
    sdk_client.remember_agent(conversation_id="conv-1", sdk_id="sdk-1", agent=cached)
    created = []

    class AgentCls:
        @staticmethod
        def resume(*_a, **_k):
            created.append("resume")
            raise AssertionError("must reuse the cached handle")

        @staticmethod
        def create(*_a, **_k):
            created.append("create")
            raise AssertionError("must reuse the cached handle")

    monkeypatch.setattr(sdk_runner, "_call_watched", lambda fn, **_k: fn())
    agent, resumed = sdk_runner._resume_or_create(
        AgentCls, "sdk-1", object(), "job-1", conversation_id="conv-1"
    )
    assert agent is cached
    assert resumed is True
    assert cached.reloads == 1
    assert created == []


def test_drop_agent_closes_the_handle_not_the_client(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    client = SimpleNamespace(closed=0)

    def close() -> None:
        client.closed += 1

    client.close = close
    sdk_client.install_client_for_tests(client)
    agent = _FakeAgent("sdk-1")
    sdk_client.remember_agent(conversation_id="conv-1", sdk_id="sdk-1", agent=agent)
    conversations.ensure("conv-1", message="hi")
    with conversations._lock:
        conversations._convs["conv-1"]["sdk_id"] = "sdk-1"
    conversations._drop("conv-1")
    assert agent.closed == 1
    assert client.closed == 0
    assert sdk_client.peek_agent(conversation_id="conv-1") is None


def test_dropping_a_merged_alias_does_not_close_the_kept_handle(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    agent = _FakeAgent("sdk-1")
    sdk_client.remember_agent(conversation_id="keep", sdk_id="sdk-1", agent=agent)
    sdk_client.remember_agent(conversation_id="extra", sdk_id="sdk-1", agent=agent)
    conversations.ensure("keep", message="keep")
    conversations.ensure("extra", message="extra")
    with conversations._lock:
        conversations._convs["keep"]["sdk_id"] = "sdk-1"
        conversations._convs["extra"]["sdk_id"] = "sdk-1"
    conversations._drop("extra")
    assert agent.closed == 0
    assert sdk_client.peek_agent(conversation_id="keep") is agent
    assert sdk_client.peek_agent(sdk_id="sdk-1") is agent


def test_cursor_me_uses_the_owned_client(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    client = object()
    sdk_client.install_client_for_tests(client)
    seen: dict[str, object] = {}

    class Cursor:
        @staticmethod
        def me(*, api_key: str, client=None):
            seen["api_key"] = api_key
            seen["client"] = client
            return SimpleNamespace(api_key_name="ok")

    monkeypatch.setitem(__import__("sys").modules, "cursor_sdk", SimpleNamespace(Cursor=Cursor))
    out = cursor_auth._cursor_me(api_key="crsr_k")
    assert seen == {"api_key": "crsr_k", "client": client}
    assert out.api_key_name == "ok"


def test_prompt_waits_for_a_send_slot_before_probing(tmp_path, monkeypatch) -> None:
    _home(tmp_path, monkeypatch)
    order: list[str] = []
    monkeypatch.setattr(sdk_runner, "wait_send_slot", lambda *_a, **_k: order.append("wait"))
    monkeypatch.setattr(cursor_auth, "assert_cursor_ready", lambda: order.append("ready"))
    monkeypatch.setattr(sdk_runner, "_prompt_sdk_once", lambda *_a, **_k: ("ok", "agent-1"))
    sdk_runner._prompt_sdk("hi")
    assert order[:2] == ["wait", "ready"]
