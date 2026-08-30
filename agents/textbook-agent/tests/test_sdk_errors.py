from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.sdk_errors import (
    ABORTED_ZH,
    AUTH_ZH,
    BUSY_ZH,
    CLOUD_ZH,
    DETACHED_ZH,
    NO_PROGRESS_ZH,
    SEND_ZH,
    STALE_TOKEN_ZH,
    STALL_ZH,
    STREAM_ZH,
    TOKEN_ZH,
    WAIT_ZH,
    HookDenied,
    hook_deny_text,
    humanize_run_fail,
    humanize_sdk_error,
    is_aborted_stream,
    is_agent_busy,
    is_auth_text,
    is_bridge_error,
    is_connect_stall,
    is_dead_thread,
    is_detached_run,
    is_stream_stall,
)

SDK_GAVE_UP = "Agent turn stopped after repeated resume attempts made no progress"


def test_humanize_readtimeout() -> None:
    err = RuntimeError("Bridge request timed out: ReadTimeout: timed out")
    assert "桥接超时" in humanize_sdk_error(err)
    assert is_bridge_error(err)


def test_humanize_bridge_failed() -> None:
    err = RuntimeError("bridge request failed")
    assert "桥接" in humanize_sdk_error(err)
    assert is_bridge_error(err)


def test_vendor_auth_prose_is_not_a_login_verdict() -> None:
    """Cursor sends this text for an expired exchanged token on a valid key."""
    err = RuntimeError(
        "Authentication error If you are logged in, try logging out and back in."
    )
    assert humanize_sdk_error(err) == STALE_TOKEN_ZH
    assert humanize_sdk_error(err) != AUTH_ZH
    assert is_auth_text(err)


def test_verified_login_failure_still_says_so() -> None:
    assert humanize_sdk_error(RuntimeError(AUTH_ZH)) == AUTH_ZH


def test_no_vendor_text_can_reach_auth_zh() -> None:
    """The contract that stops the next wording change becoming a false alarm.

    Only ``cursor_auth`` may produce ``AUTH_ZH``, and it does so from a probe
    result rather than from prose. Nothing Cursor can say about a run may.
    """
    vendor = [
        "Authentication error If you are logged in, try logging out and back in.",
        "ERROR_NOT_LOGGED_IN",
        "unauthorized",
        "invalid api key",
        "Connection stalled repeatedly",
        "Connection failed repeatedly",
        "bidi_append_deadline_exceeded",
        "internal: something we have never seen",
        "AuthenticationError: gRPC status 16 UNAUTHENTICATED",
    ]
    for text in vendor:
        assert humanize_sdk_error(RuntimeError(text)) != AUTH_ZH, text


def test_stream_stall_is_transport_not_auth() -> None:
    err = RuntimeError("Connection stalled repeatedly")
    assert is_stream_stall(err)
    assert not is_auth_text(err)
    assert humanize_sdk_error(err) == STREAM_ZH
    # must reach auto_resume so the user gets one breakpoint continue
    assert is_connect_stall(err)


def test_aborted_stream_is_not_a_cloud_verdict() -> None:
    err = RuntimeError("[canceled] This operation was aborted")
    assert is_aborted_stream(err)
    assert humanize_sdk_error(err) == ABORTED_ZH
    assert CLOUD_ZH not in humanize_sdk_error(err)
    assert humanize_sdk_error(RuntimeError(ABORTED_ZH)) == ABORTED_ZH
    assert is_connect_stall(err)


def test_user_stop_is_not_an_aborted_bridge() -> None:
    class JobCancelled(RuntimeError):
        pass

    assert humanize_sdk_error(JobCancelled()) == "已停止"
    assert not is_aborted_stream(JobCancelled())


def test_unknown_failure_is_neutral_plus_raw() -> None:
    out = humanize_sdk_error(RuntimeError("internal: brand new failure mode"))
    assert out.startswith(CLOUD_ZH)
    assert "brand new failure mode" in out


def test_humanize_token_error_before_bridge_exited() -> None:
    err = RuntimeError("Bridge exited … Missing value for --tool-callback-auth-token")
    assert humanize_sdk_error(err) == TOKEN_ZH
    assert is_bridge_error(err)


def test_hook_denied_is_not_bridge() -> None:
    err = HookDenied("权限拒绝：不能读")
    assert not is_bridge_error(err)
    assert "权限拒绝" in humanize_sdk_error(err)


def test_passthrough_chinese() -> None:
    assert humanize_sdk_error(TimeoutError(STALL_ZH)) == STALL_ZH


def test_is_connect_stall_only_bridge() -> None:
    assert is_connect_stall(TimeoutError(STALL_ZH))
    assert is_connect_stall(RuntimeError("Bridge request timed out: ReadTimeout"))
    assert not is_connect_stall(TimeoutError(WAIT_ZH))
    assert not is_connect_stall(TimeoutError(SEND_ZH))
    assert not is_connect_stall(HookDenied("权限拒绝：不能读"))
    assert not is_connect_stall("")
    assert not is_connect_stall(BUSY_ZH)


def test_is_detached_run() -> None:
    err = RuntimeError("internal: Cannot wait on a detached running run")
    assert is_detached_run(err)
    assert humanize_sdk_error(err) == DETACHED_ZH
    assert not is_detached_run(TimeoutError(STALL_ZH))


def test_is_agent_busy_internal_active_run() -> None:
    err = RuntimeError("internal: Agent f97254ee-a8b1-44ee-a83e-a3ac45e7617e already has active run")
    assert is_agent_busy(err)
    assert humanize_sdk_error(err) == BUSY_ZH
    assert not is_agent_busy(TimeoutError(STALL_ZH))


def test_hook_deny_text_from_message() -> None:
    msg = SimpleNamespace(
        type="tool_call",
        name="Read",
        permission="deny",
        user_message="不能读 .cursor/projects",
        args={},
    )
    text = hook_deny_text(msg)
    assert "权限拒绝" in text
    assert ".cursor/projects" in text


def test_hook_deny_text_ignores_normal() -> None:
    msg = SimpleNamespace(type="assistant", text="ok", args={})
    assert hook_deny_text(msg) == ""


def test_hook_deny_text_keeps_user_message_when_agent_message_present() -> None:
    msg = SimpleNamespace(
        type="tool_call",
        name="Write",
        permission="deny",
        user_message="这次改动需要你在授权卡上确认",
        agent_message="先调 request_code_edit，把四段说明写清楚。",
        args={"path": "tools/foo.py"},
    )
    text = hook_deny_text(msg)
    assert text.startswith("权限拒绝")
    assert "授权卡" in text
    assert not is_connect_stall(text)


def test_hook_deny_text_ignores_file_that_mentions_deny() -> None:
    """Reading the hook script is a successful Read, not a hook denial."""
    src = (
        Path(__file__).resolve().parents[1]
        / "workspace"
        / ".cursor"
        / "hooks"
        / "guard-redline.py"
    )
    text = src.read_text(encoding="utf-8")
    assert '"permission":"deny"' in text or '"permission": "deny"' in text
    msg = SimpleNamespace(
        type="tool_call",
        name="Read",
        status="completed",
        permission=None,
        result=text,
        args={"path": "/data/agent/.cursor/hooks/guard-redline.py"},
    )
    assert hook_deny_text(msg) == ""


def test_humanize_run_fail_reads_status_error_trace() -> None:
    result = SimpleNamespace(id="run-b6282354-05ca-4b42-883c-39e1fcf3e5f4", status="error")
    traces = [
        {"title": "resume", "detail": "接上同一条 Cursor 对话"},
        {
            "title": "status ERROR",
            "detail": "Authentication error If you are logged in, try logging out and back in.",
        },
    ]
    assert humanize_run_fail(result, traces) == STALE_TOKEN_ZH
    assert result.id in humanize_run_fail(result, [])
    assert is_auth_text(traces[-1]["detail"])
    assert not is_connect_stall(AUTH_ZH)
    # the stale-token message is recoverable, so it must reach auto_resume
    assert is_connect_stall(STALE_TOKEN_ZH)


def test_sdk_give_up_is_named_not_swallowed_by_the_neutral_fallback() -> None:
    err = RuntimeError(SDK_GAVE_UP)
    assert is_dead_thread(err)
    assert humanize_sdk_error(err) == NO_PROGRESS_ZH
    assert CLOUD_ZH not in humanize_sdk_error(err)
    # our own wording has to survive a second pass unchanged
    assert humanize_sdk_error(RuntimeError(NO_PROGRESS_ZH)) == NO_PROGRESS_ZH


def test_sdk_give_up_counts_as_a_stream_failure() -> None:
    """The breaker only counts stream-class failures, so this must land there."""
    assert is_stream_stall(SDK_GAVE_UP)
    assert is_stream_stall("Resume attempts are streaming but not producing checkpoints; "
                           "stopping automatic resume")


def test_sdk_give_up_is_not_an_auth_or_bridge_problem() -> None:
    err = RuntimeError(SDK_GAVE_UP)
    assert not is_auth_text(err)
    assert not is_bridge_error(err)


def test_a_plain_stream_stall_is_still_recoverable_not_a_retirement() -> None:
    err = RuntimeError("Connection stalled repeatedly")
    assert is_stream_stall(err)
    assert not is_dead_thread(err)
    assert humanize_sdk_error(err) == STREAM_ZH


def test_run_fail_names_the_give_up_from_the_status_trace() -> None:
    result = SimpleNamespace(id="run-ab2b0ae4-4da1-460a-a5f3-cad9dab684a8", status="error")
    traces = [
        {"title": "resume", "detail": "接上同一条 Cursor 对话"},
        {"title": "status ERROR", "detail": SDK_GAVE_UP},
    ]
    assert humanize_run_fail(result, traces) == NO_PROGRESS_ZH
