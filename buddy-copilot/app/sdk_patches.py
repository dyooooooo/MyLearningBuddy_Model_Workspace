"""cursor-sdk 运行时补丁（在导入 Agent 之前调用）。

已知问题：Python SDK 用 secrets.token_urlsafe(32) 生成
--tool-callback-auth-token / --store-callback-auth-token；
桥端 takeValue 把以 '-' 开头的值当成「缺参数」，约 1/64 概率
Bridge exited … Missing value for --tool-callback-auth-token。

上游确认中；在此把 token 生成改成不以 '-' 开头，并可选重试。
见 https://forum.cursor.com/t/163586
"""

from __future__ import annotations

import logging
import secrets

log = logging.getLogger("copilot.sdk_patches")

_APPLIED = False

BRIDGE_AUTH_TOKEN_DASH_ERR = "Missing value for --tool-callback-auth-token"
# store-callback 同源缺陷（启用本地 store 时）
BRIDGE_STORE_AUTH_TOKEN_DASH_ERR = "Missing value for --store-callback-auth-token"


def safe_bridge_auth_token() -> str:
    for _ in range(32):
        token = secrets.token_urlsafe(32)
        if token and not token.startswith("-"):
            return token
    return "x" + secrets.token_urlsafe(32).lstrip("-")


def is_bridge_auth_token_argv_error(exc: BaseException) -> bool:
    text = str(exc)
    return (
        BRIDGE_AUTH_TOKEN_DASH_ERR in text
        or BRIDGE_STORE_AUTH_TOKEN_DASH_ERR in text
    )


def apply_cursor_sdk_patches() -> None:
    """幂等：替换 cursor_sdk 内 callback token 生成器。"""
    global _APPLIED
    if _APPLIED:
        return
    try:
        from cursor_sdk import _store_callback, _tool_callback
    except ImportError:
        log.warning("cursor_sdk 不可用，跳过 bridge auth token 补丁")
        return

    _tool_callback._new_auth_token = safe_bridge_auth_token  # type: ignore[attr-defined]
    _store_callback._new_auth_token = safe_bridge_auth_token  # type: ignore[attr-defined]
    _APPLIED = True
    log.info("已应用 cursor-sdk bridge auth token 补丁（禁止 '-' 前缀）")
