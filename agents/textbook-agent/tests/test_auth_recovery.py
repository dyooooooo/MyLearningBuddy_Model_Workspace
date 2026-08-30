"""Contract for the one thing that kept misfiring: what counts as a lost login.

Cursor sends "Authentication error … try logging out and back in" for an expired
short-lived token exchanged from a perfectly valid API key, and confirmed that
only a fresh bridge process clears it. So the run path must verify the key
against the cloud before it blames the credential, and must restart the bridge
rather than rewrite auth.json.
"""

from __future__ import annotations

import pytest

from app import cursor_auth, jobs, sdk_runner, transport_breaker
from app.sdk_errors import AUTH_ZH, STALL_ZH

VENDOR_AUTH = "Authentication error If you are logged in, try logging out and back in."


@pytest.fixture
def rig(monkeypatch):
    """Stub everything around ``_prompt_sdk`` so only its recovery logic runs."""
    calls: list[dict] = []
    recovered: list[int] = []
    rewrote: list[int] = []

    monkeypatch.setattr(cursor_auth, "assert_cursor_ready", lambda: None)
    monkeypatch.setattr(cursor_auth, "ensure_cursor_credentials", lambda: rewrote.append(1))
    monkeypatch.setattr(sdk_runner, "wait_send_slot", lambda *_a, **_k: None)
    monkeypatch.setattr(sdk_runner, "recover_bridge", lambda *_a, **_k: recovered.append(1))
    jobs.reset_for_tests()
    transport_breaker.reset()

    def scripted(*outcomes):
        """Make ``_prompt_sdk_once`` yield ``outcomes`` in order, recording args."""
        seq = list(outcomes)

        def fake(prompt, **kw):
            calls.append(kw)
            out = seq.pop(0) if seq else ("ok", "agent-1")
            if isinstance(out, BaseException):
                raise out
            return out

        monkeypatch.setattr(sdk_runner, "_prompt_sdk_once", fake)

    def key(live: bool):
        monkeypatch.setattr(cursor_auth, "key_is_live", lambda: live)

    yield {
        "calls": calls,
        "recovered": recovered,
        "rewrote": rewrote,
        "scripted": scripted,
        "key": key,
    }
    transport_breaker.reset()


def test_vendor_auth_prose_restarts_the_bridge_and_retries(rig) -> None:
    rig["key"](True)
    rig["scripted"](RuntimeError(VENDOR_AUTH), ("done", "agent-1"))

    reply, agent_id = sdk_runner._prompt_sdk("hi", resume_id="agent-1")

    assert (reply, agent_id) == ("done", "agent-1")
    assert rig["recovered"] == [1], "only a fresh bridge process clears a stale token"
    assert rig["rewrote"] == [], "rewriting auth.json cannot fix an API-key host"
    assert len(rig["calls"]) == 2


def test_the_retry_resumes_the_same_thread(rig) -> None:
    rig["key"](True)
    rig["scripted"](RuntimeError(VENDOR_AUTH), ("done", "agent-1"))

    sdk_runner._prompt_sdk("hi", resume_id="agent-1")

    assert [c["resume_id"] for c in rig["calls"]] == ["agent-1", "agent-1"]


def test_a_rejected_key_is_the_only_login_failure(rig) -> None:
    rig["key"](False)
    rig["scripted"](RuntimeError(VENDOR_AUTH))

    with pytest.raises(RuntimeError) as err:
        sdk_runner._prompt_sdk("hi", resume_id="agent-1")

    assert str(err.value) == AUTH_ZH
    assert rig["recovered"] == [], "no point restarting the bridge for a bad key"
    assert len(rig["calls"]) == 1


def test_a_live_key_never_yields_a_login_failure(rig) -> None:
    rig["key"](True)
    rig["scripted"](RuntimeError(VENDOR_AUTH), RuntimeError(VENDOR_AUTH))

    with pytest.raises(RuntimeError) as err:
        sdk_runner._prompt_sdk("hi", resume_id="agent-1")

    assert str(err.value) != AUTH_ZH
    assert "登录失效" not in str(err.value)


def test_non_auth_failures_are_left_alone(rig) -> None:
    rig["key"](True)
    rig["scripted"](RuntimeError(STALL_ZH))

    with pytest.raises(RuntimeError):
        sdk_runner._prompt_sdk("hi", resume_id="agent-1")

    assert len(rig["calls"]) == 1, "no auth retry for a plain transport failure"


def test_transport_failures_burn_a_strike(rig) -> None:
    rig["key"](True)
    for _ in range(transport_breaker.LIMIT):
        rig["scripted"](RuntimeError("Connection stalled repeatedly"))
        with pytest.raises(RuntimeError):
            sdk_runner._prompt_sdk("hi", resume_id="agent-1")
    assert transport_breaker.is_tripped("agent-1")


def test_a_tripped_thread_is_dropped_for_a_fresh_one(rig) -> None:
    rig["key"](True)
    for _ in range(transport_breaker.LIMIT):
        transport_breaker.note_failure("agent-1")
    rig["scripted"](("done", "agent-2"))

    reply, agent_id = sdk_runner._prompt_sdk("hi", resume_id="agent-1")

    assert (reply, agent_id) == ("done", "agent-2")
    assert rig["calls"][0]["resume_id"] == "", "must stop resuming a dead thread"
    assert rig["recovered"] == [1]
    assert not transport_breaker.is_tripped("agent-1")


def test_a_good_turn_clears_the_strikes(rig) -> None:
    rig["key"](True)
    transport_breaker.note_failure("agent-1")
    rig["scripted"](("done", "agent-1"))

    sdk_runner._prompt_sdk("hi", resume_id="agent-1")

    transport_breaker.note_failure("agent-1")
    assert not transport_breaker.is_tripped("agent-1")
