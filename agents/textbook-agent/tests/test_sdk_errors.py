from __future__ import annotations

from types import SimpleNamespace

from app.sdk_errors import (
    STALL_ZH,
    HookDenied,
    hook_deny_text,
    humanize_sdk_error,
    is_bridge_error,
)


def test_humanize_readtimeout() -> None:
    err = RuntimeError("Bridge request timed out: ReadTimeout: timed out")
    assert "桥接超时" in humanize_sdk_error(err)
    assert is_bridge_error(err)


def test_humanize_bridge_failed() -> None:
    err = RuntimeError("bridge request failed")
    assert "桥接" in humanize_sdk_error(err)
    assert is_bridge_error(err)


def test_hook_denied_is_not_bridge() -> None:
    err = HookDenied("权限拒绝：不能读")
    assert not is_bridge_error(err)
    assert "权限拒绝" in humanize_sdk_error(err)


def test_passthrough_chinese() -> None:
    assert humanize_sdk_error(TimeoutError(STALL_ZH)) == STALL_ZH


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
