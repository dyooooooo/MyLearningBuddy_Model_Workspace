from __future__ import annotations

from app.sdk_patches import (
    apply_cursor_sdk_patches,
    is_bridge_auth_token_argv_error,
    safe_bridge_auth_token,
)


def test_safe_bridge_auth_token_never_starts_with_dash() -> None:
    for _ in range(200):
        token = safe_bridge_auth_token()
        assert token
        assert not token.startswith("-")


def test_is_bridge_auth_token_argv_error() -> None:
    assert is_bridge_auth_token_argv_error(RuntimeError("Missing value for --tool-callback-auth-token"))
    assert is_bridge_auth_token_argv_error(RuntimeError("Missing value for --store-callback-auth-token"))
    assert not is_bridge_auth_token_argv_error(RuntimeError("connection refused"))


def test_apply_patches_idempotent() -> None:
    apply_cursor_sdk_patches()
    apply_cursor_sdk_patches()
