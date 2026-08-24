"""Translate Cursor SDK / bridge failures into short Chinese errors."""

from __future__ import annotations

import json
import re
from typing import Any

STALL_ZH = "Cursor 桥接超时，云端或本地桥没有回包"
WAIT_ZH = "Cursor 任务超时，超过等待上限仍没有最终回答"
BRIDGE_FAIL_ZH = "Cursor 本地桥接失败，请重试"
TOKEN_ZH = "Cursor 桥接认证参数异常，请重试"
DENY_ZH = "权限拒绝：钩子不允许这次操作"


class HookDenied(RuntimeError):
    """Hook returned permission=deny (user_message)."""


def humanize_sdk_error(exc: BaseException) -> str:
    if type(exc).__name__ in {"JobCancelled"}:
        return "已停止"
    text = str(exc or "").strip()
    if not text:
        return STALL_ZH
    if "桥接超时" in text or "没有回包" in text or "任务超时" in text:
        return text[:400]
    if "权限拒绝" in text:
        return text[:400]
    if "本地桥接失败" in text or "桥接认证" in text:
        return text[:400]
    low = text.lower()
    if (
        "readtimeout" in low
        or "bridge request timed out" in low
        or "timed out" in low and "bridge" in low
    ):
        return STALL_ZH
    if "bridge request failed" in low or "bridge exited" in low:
        return BRIDGE_FAIL_ZH
    if (
        "missing value for --tool-callback-auth-token" in low
        or "missing value for --store-callback-auth-token" in low
    ):
        return TOKEN_ZH
    if isinstance(exc, TimeoutError) or "exceeded" in low and "timeout" in low:
        return WAIT_ZH if "exceeded" in low or "等待上限" in text else STALL_ZH
    return text[:800]


def is_bridge_error(exc: BaseException) -> bool:
    if isinstance(exc, HookDenied):
        return False
    text = str(exc).lower()
    return (
        "readtimeout" in text
        or "bridge request timed out" in text
        or "bridge request failed" in text
        or "bridge exited" in text
        or "missing value for --tool-callback-auth-token" in text
        or "missing value for --store-callback-auth-token" in text
        or ("timed out" in text and "bridge" in text)
    )


def _blob(message: Any) -> str:
    parts: list[str] = []
    for name in (
        "type",
        "permission",
        "user_message",
        "status",
        "text",
        "message",
        "result",
        "output",
        "args",
        "error",
    ):
        val = getattr(message, name, None)
        if val is None:
            continue
        if isinstance(val, (str, int, float)):
            parts.append(f"{name}={val}")
            continue
        try:
            parts.append(json.dumps(val, ensure_ascii=False, default=str)[:2000])
        except TypeError:
            parts.append(str(val)[:800])
    return "\n".join(parts)


def hook_deny_text(message: Any) -> str:
    blob = _blob(message)
    low = blob.lower()
    denied = (
        ("permission" in low and "deny" in low)
        or "permission_denied" in low
        or '"permission": "deny"' in low
        or "'permission': 'deny'" in low
    )
    if not denied:
        return ""
    um = getattr(message, "user_message", None)
    if um:
        return f"权限拒绝：{um}"
    match = re.search(r"user_message['\"]?\s*[:=]\s*['\"]([^'\"]+)", blob)
    if match:
        return f"权限拒绝：{match.group(1)}"
    return DENY_ZH
