"""cursor-sdk bridge auth token 补丁。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sdk_patches import (  # noqa: E402
    apply_cursor_sdk_patches,
    is_bridge_auth_token_argv_error,
    safe_bridge_auth_token,
)


def test_safe_bridge_auth_token_never_starts_with_dash():
    for _ in range(200):
        t = safe_bridge_auth_token()
        assert t
        assert not t.startswith("-")


def test_is_bridge_auth_token_argv_error():
    assert is_bridge_auth_token_argv_error(
        RuntimeError("Missing value for --tool-callback-auth-token")
    )
    assert is_bridge_auth_token_argv_error(
        RuntimeError("Missing value for --store-callback-auth-token")
    )
    assert not is_bridge_auth_token_argv_error(RuntimeError("connection refused"))


def test_apply_patches_idempotent():
    apply_cursor_sdk_patches()
    apply_cursor_sdk_patches()
    from cursor_sdk import _tool_callback

    t = _tool_callback._new_auth_token()
    assert t and not t.startswith("-")
