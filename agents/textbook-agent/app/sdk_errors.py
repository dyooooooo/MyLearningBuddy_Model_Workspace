"""Translate Cursor SDK / bridge failures into short Chinese errors.

Vendor error prose is a *hint*, never a verdict. Cursor confirmed that an
expired short-lived token exchanged from a valid API key surfaces as
"Authentication error … try logging out and back in", and that a dropped
HTTP/2 agent stream surfaces as "Connection stalled repeatedly". Neither says
anything about the credential. So ``AUTH_ZH`` is reserved for one producer: a
verified ``Cursor.me()`` rejection in ``cursor_auth``. Everything unrecognised
falls back to a neutral message plus the raw text, which is what keeps a new
vendor wording from turning into a new false "login expired".
"""

from __future__ import annotations

from typing import Any

STALL_ZH = "Cursor 桥接超时，云端或本地桥没有回包"
WAIT_ZH = "Cursor 任务超时，超过等待上限仍没有最终回答"
SEND_ZH = "Cursor 发送超时，桥还在，请从断点继续"
BRIDGE_FAIL_ZH = "Cursor 本地桥接失败，请重试"
TOKEN_ZH = "Cursor 桥接认证参数异常，请重试"
AUTH_ZH = "Cursor 登录失效，请检查 CURSOR_API_KEY"
STALE_TOKEN_ZH = "Cursor 云端令牌过期（钥匙仍有效），已重开本地桥，请从断点继续"
STREAM_ZH = "Cursor 云端连接反复中断，断点已保留，请从断点继续"
NO_PROGRESS_ZH = "Cursor 连接反复中断、进度没存住，这条线程已停用；已完成的部分都在，下一轮会自动换新线程"
CLOUD_ZH = "Cursor 云端这轮没跑通，断点已保留"
ABORTED_ZH = "Cursor 共享桥被重置，另一条对话的探活或关桥拆了这条流，断点已保留"
DENY_ZH = "权限拒绝：钩子不允许这次操作"
BUSY_ZH = "上一轮 Cursor 任务还在跑，接到前台失败，请再点一次"
DETACHED_ZH = "上一轮残留任务无法接到前台，已失效"

# Fragments that identify a message we already produced, so it passes through
# instead of being re-wrapped by the neutral fallback. Fragments rather than the
# full constants because some are formatted with a live number ("超过 1800 秒").
_OUR_MARKERS = (
    "桥接超时",
    "没有回包",
    "任务超时",
    "发送超时",
    "本地桥接失败",
    "桥接认证",
    "令牌过期",
    "连接反复中断",
    "进度没存住",
    "云端这轮没跑通",
    "共享桥被重置",
)


class HookDenied(RuntimeError):
    """Hook returned permission=deny (user_message)."""


def humanize_sdk_error(exc: BaseException) -> str:
    if type(exc).__name__ in {"JobCancelled"}:
        return "已停止"
    text = str(exc or "").strip()
    if not text:
        return STALL_ZH
    if is_detached_run(text):
        return DETACHED_ZH
    if "already has active run" in text.lower() or "agent_busy" in text.lower():
        return BUSY_ZH
    if any(mark in text for mark in _OUR_MARKERS):
        return text[:400]
    if "权限拒绝" in text:
        return text[:400]
    # Only a verified probe rejection produces this text; when it comes back
    # around, keep it.
    if text == AUTH_ZH or "登录失效" in text:
        return AUTH_ZH
    low = text.lower()
    if (
        "missing value for --tool-callback-auth-token" in low
        or "missing value for --store-callback-auth-token" in low
    ):
        return TOKEN_ZH
    if is_dead_thread(text):
        return NO_PROGRESS_ZH
    if is_aborted_stream(text):
        return ABORTED_ZH
    if is_stream_stall(text):
        return STREAM_ZH
    # Vendor prose claiming auth. Officially a misclassification of an expired
    # exchanged token, so never escalate it to AUTH_ZH here: the caller checks
    # the key against the cloud and decides.
    if is_auth_text(text):
        return STALE_TOKEN_ZH
    if (
        "readtimeout" in low
        or "bridge request timed out" in low
        or "timed out" in low and "bridge" in low
    ):
        return STALL_ZH
    if (
        "bridge request failed" in low
        or "bridge exited" in low
        or "connection refused" in low
        or "connecterror" in low
    ):
        return BRIDGE_FAIL_ZH
    if isinstance(exc, TimeoutError) or "exceeded" in low and "timeout" in low:
        return WAIT_ZH if "exceeded" in low or "等待上限" in text else STALL_ZH
    return f"{CLOUD_ZH}：{text[:400]}"


def is_bridge_error(exc: BaseException) -> bool:
    """Transport-level failure of the local cursor-sdk-bridge — restart and retry.

    Keep this in sync with ``humanize_sdk_error``. A dead bridge surfaces as
    ``NetworkError: Bridge request failed: ConnectError: [Errno 111] Connection
    refused``; that is a process problem, never a credential problem.
    """
    if isinstance(exc, HookDenied):
        return False
    text = str(exc).lower()
    return (
        "readtimeout" in text
        or "bridge request timed out" in text
        or "bridge request failed" in text
        or "bridge exited" in text
        or "connection refused" in text
        or "connecterror" in text
        or "missing value for --tool-callback-auth-token" in text
        or "missing value for --store-callback-auth-token" in text
        or ("timed out" in text and "bridge" in text)
    )


def is_detached_run(exc: BaseException | str) -> bool:
    """True when get_run/list_runs returned a leftover handle that cannot wait/stream."""
    text = str(exc or "")
    low = text.lower()
    return (
        "detached running run" in low
        or "cannot wait on a detached" in low
        or "no run event store" in low
        or text == DETACHED_ZH
    )


def is_agent_busy(exc: BaseException | str) -> bool:
    name = type(exc).__name__ if isinstance(exc, BaseException) else ""
    if name == "AgentBusyError":
        return True
    text = str(exc or "")
    low = text.lower()
    return (
        "already has active run" in low
        or "agent_busy" in low
        or "上一轮 Cursor 任务还在跑" in text
        or text == BUSY_ZH
    )


def is_auth_text(exc: BaseException | str) -> bool:
    """The failure *reads* like an auth problem. A hint, not a verdict.

    Cursor confirmed this prose also covers an expired short-lived token
    exchanged from a perfectly valid API key, so a caller must verify the key
    against the cloud before telling the user the login is gone. The only sound
    verdict lives in ``cursor_auth.key_is_live``.
    """
    text = str(exc or "")
    low = text.lower()
    return (
        "authentication error" in low
        or "logging out and back in" in low
        or "unauthorized" in low
        or "invalid api key" in low
        or "error_not_logged_in" in low
        or text == AUTH_ZH
        or "登录失效" in text
    )


def is_aborted_stream(exc: BaseException | str) -> bool:
    """The shared local bridge was reset while this send was still on it.

    Surfaces as ``[canceled] This operation was aborted``. That is our process
    tearing the default client down — not a cloud-side "run failed" verdict,
    and not the user tapping Stop (that is ``JobCancelled`` → 已停止).
    """
    text = str(exc or "")
    low = text.lower()
    return (
        "this operation was aborted" in low
        or "[canceled]" in low
        or "operation was aborted" in low
        or text == ABORTED_ZH
        or "共享桥被重置" in text
    )


def is_dead_thread(exc: BaseException | str) -> bool:
    """The SDK itself gave up on this Cursor thread — resuming it again is futile.

    Raised by the vendored bridge once two consecutive auto-resumes stream data
    without producing a new checkpoint ("Resume attempts are streaming but not
    producing checkpoints; stopping automatic resume"). The SDK has already
    retried, so unlike a plain stream stall this must not earn a mere strike: the
    thread has to be abandoned at once, or every later turn resumes a handle the
    cloud no longer advances and hangs until the eval timeout.
    """
    text = str(exc or "")
    low = text.lower()
    return (
        "repeated resume attempts" in low
        or "made no progress" in low
        or "stopping automatic resume" in low
        or "进度没存住" in text
    )


def is_stream_stall(exc: BaseException | str) -> bool:
    """Cursor's own agent stream gave up: the long-lived HTTP/2 bidi stream died.

    In a container this is normally NAT / proxy idle-timeout on the stream, not
    a credential or a local bridge problem. The run is resumable from its
    checkpoint, so this must reach ``is_connect_stall`` and trigger one
    breakpoint continue.
    """
    text = str(exc or "")
    low = text.lower()
    return (
        "connection stalled" in low
        or "connection failed repeatedly" in low
        or "bidi_append_deadline_exceeded" in low
        or text == STREAM_ZH
        or is_dead_thread(text)
    )


def run_fail_blob(result: Any, traces: list[Any] | None = None) -> str:
    """Cursor RunResult has no error field; auth text lives on the status stream."""
    rid = str(getattr(result, "id", "") or "")
    for step in reversed(list(traces or [])):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "")
        detail = str(step.get("detail") or "").strip()
        if "error" in title.lower() and detail:
            return detail
    for name in ("error", "message"):
        val = getattr(result, name, None)
        if callable(val) or val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return f"cursor agent run failed: {rid}"


def humanize_run_fail(result: Any, traces: list[Any] | None = None) -> str:
    return humanize_sdk_error(RuntimeError(run_fail_blob(result, traces)))


def is_connect_stall(exc: BaseException | str) -> bool:
    """True for transport failures one breakpoint continue can recover.

    Covers the local bridge dying, Cursor's own agent stream stalling, and an
    expired exchanged token — all of which leave the run's checkpoint intact.
    Excludes long eval waits, hook denies, and a verified login failure, none of
    which a retry can fix.
    """
    text = str(exc or "")
    if not text:
        return False
    if any(bit in text for bit in ("任务超时", "等待上限", "发送超时", "权限拒绝", "桥接认证", "登录失效")):
        return False
    if is_auth_text(text):
        return False
    low = text.lower()
    if any(bit in low for bit in ("tool-callback-auth-token", "store-callback-auth-token")):
        return False
    if is_stream_stall(text) or is_aborted_stream(text):
        return True
    if any(bit in text for bit in ("桥接超时", "没有回包", "本地桥接失败", STALE_TOKEN_ZH)):
        return True
    return isinstance(exc, BaseException) and is_bridge_error(exc)


def _permission_field(message: Any) -> str:
    """Structured ``permission`` only — never search tool output text.

    ``guard-redline.py`` itself contains the deny JSON. A successful Read of
    that file used to match ``"permission": "deny"`` in the contents and abort
    the run as if the hook had blocked the read.
    """
    for obj in (message, getattr(message, "result", None)):
        if obj is None or isinstance(obj, str):
            continue
        val = obj.get("permission") if isinstance(obj, dict) else getattr(obj, "permission", None)
        if val is not None:
            return str(val).strip().lower()
    return ""


def hook_deny_text(message: Any) -> str:
    if _permission_field(message) != "deny":
        return ""
    um = getattr(message, "user_message", None)
    if um:
        return f"权限拒绝：{um}"
    result = getattr(message, "result", None)
    if isinstance(result, dict) and result.get("user_message"):
        return f"权限拒绝：{result['user_message']}"
    return DENY_ZH
