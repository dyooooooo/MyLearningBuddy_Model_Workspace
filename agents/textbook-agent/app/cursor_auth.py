"""Persist Cursor CLI / SDK credentials under agent home.

The local cursor-agent looks for ``~/.config/cursor/auth.json``. Docker has no
keychain, so compose sets ``AGENT_CLI_CREDENTIAL_STORE=file``.

This process is API-key-only. The file store must not keep OAuth
``accessToken`` / ``refreshToken``: cursor-agent prefers those session tokens
over ``CURSOR_API_KEY``, and when they expire the run dies with
"Authentication error … try logging out and back in" even though the key is
still valid. Cursor.me() is the official cloud probe.

Cursor.me() reaches the cloud *through the local cursor-sdk-bridge*, so a dead
bridge fails it with ConnectError just like a bad key would. Never report a
bridge failure as an expired login, and never let it block a run: the bridge is
recoverable (sdk_runner.recover_bridge) and the login is not.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from app.config import settings
from app.sdk_errors import AUTH_ZH, humanize_sdk_error, is_auth_text, is_bridge_error

logger = logging.getLogger(__name__)

CLI_BACKEND = "https://api2.cursor.sh"
_PROBE_TTL_S = 45.0
# A dead bridge is transient and self-healing; caching it would keep every run
# in the next TTL window reporting a stale failure.
_PROBE_BRIDGE_TTL_S = 3.0
_probe_at = 0.0
_probe_ttl = _PROBE_TTL_S
_probe_cache: dict[str, Any] | None = None


def live_api_key() -> str:
    return (os.environ.get("CURSOR_API_KEY") or settings.cursor_api_key or "").strip()


def ensure_cursor_credentials() -> None:
    key = live_api_key()
    if not key:
        logger.warning("CURSOR_API_KEY is empty; local Cursor agent will fail auth")
        return
    _write_json(_cli_auth_path(), _merge_cli_auth(_cli_auth_path(), key), 0o600)
    _write_json(_sdk_auth_path(), _merge_sdk_auth(_sdk_auth_path(), key), 0o600)
    logger.info("Cursor file credentials ready under agent home (API key only)")


def assert_cursor_ready() -> None:
    """Block a run only for what a retry cannot fix: a missing or rejected key.

    A bridge/network failure is deliberately allowed through so the caller's own
    ``is_bridge_error`` → ``recover_bridge`` → retry path can heal it. Gating on
    it here would turn a restartable bridge into a permanent "login expired".
    """
    key = live_api_key()
    if not key:
        raise RuntimeError("CURSOR_API_KEY is empty")
    ensure_cursor_credentials()
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    status = probe_status()
    if status.get("reason") == "auth":
        raise RuntimeError(AUTH_ZH)
    if not status.get("cloud_ok"):
        logger.warning(
            "Cursor.me probe not ok (reason=%s): %s — letting the run retry the bridge",
            status.get("reason"),
            status.get("detail"),
        )


def key_is_live() -> bool:
    """The only sound answer to "is the credential actually gone?".

    ``Cursor.me()`` keeps working while ``agent.send()`` is already dead on an
    expired exchanged token, which is exactly what makes it a usable
    discriminator: a run that failed with auth-looking prose while this returns
    True was never a credential problem. A bridge failure is not evidence
    either way, so it counts as live and lets the caller restart the bridge.
    """
    if not live_api_key():
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return probe_status(force=True).get("reason") != "auth"


def probe_status(*, force: bool = False) -> dict[str, Any]:
    """Cheap readiness snapshot. Never raises. Do not put this on compose health.

    ``reason`` is what callers must branch on: ``auth`` means the key itself was
    rejected, ``bridge`` means the local bridge never answered.
    """
    global _probe_at, _probe_ttl, _probe_cache
    now = time.monotonic()
    if not force and _probe_cache is not None and now - _probe_at < _probe_ttl:
        return dict(_probe_cache)
    key = live_api_key()
    out: dict[str, Any] = {
        "key_present": bool(key),
        "key_len": len(key),
        "session_tokens": _cli_has_session_tokens(),
        "cloud_ok": False,
        "reason": "empty",
        "detail": "CURSOR_API_KEY is empty",
    }
    if key:
        out.update(_probe_cloud(key))
    _probe_cache, _probe_at = out, now
    _probe_ttl = _PROBE_BRIDGE_TTL_S if out["reason"] == "bridge" else _PROBE_TTL_S
    return dict(out)


def _cursor_me(*, api_key: str) -> Any:
    from cursor_sdk import Cursor

    from app.sdk_client import ensure_client

    client = ensure_client()
    if client is not None:
        return Cursor.me(api_key=api_key, client=client)
    return Cursor.me(api_key=api_key)


def _probe_cloud(key: str) -> dict[str, Any]:
    """Call Cursor.me(); on a bridge failure drop the dead bridge and retry once."""
    for attempt in range(2):
        try:
            me = _cursor_me(api_key=key)
            name = str(getattr(me, "api_key_name", "") or "").strip()
            return {"cloud_ok": True, "reason": "ok", "detail": name or "cloud ok"}
        except Exception as exc:
            bridge = is_bridge_error(exc) and not is_auth_text(exc)
            logger.warning("Cursor.me probe failed (bridge=%s): %s", bridge, exc)
            if bridge and attempt == 0 and _drop_dead_bridge():
                continue
            reason = "bridge" if bridge else ("auth" if is_auth_text(exc) else "unknown")
            return {"cloud_ok": False, "reason": reason, "detail": humanize_sdk_error(exc)}
    return {"cloud_ok": False, "reason": "bridge", "detail": "Cursor 本地桥接失败，请重试"}


def _drop_dead_bridge() -> bool:
    """Release the cached SDK client so the next call spawns a fresh bridge.

    Never do this while another conversation is on the same client — that is
    what aborts the sibling stream with ``This operation was aborted``.
    """
    try:
        from app.bridge_cleanup import sweep_orphan_bridges
        from app.sdk_client import close_owned_client, ensure_client
        from app.sdk_runner import count_held_slots

        if count_held_slots() > 0:
            logger.info("skip dropping bridge; another send is on the shared bridge")
            return False
        close_owned_client()
        sweep_orphan_bridges(grace_s=1.0)
        ensure_client()
        return True
    except Exception:
        logger.warning("close_sdk_bridge before probe retry failed", exc_info=True)
        return False


def _cli_auth_path():
    return settings.home / ".config" / "cursor" / "auth.json"


def _sdk_auth_path():
    return settings.cursor_dir / "sdk" / "auth.json"


def _read_object(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _cli_has_session_tokens() -> bool:
    data = _read_object(_cli_auth_path())
    return bool(str(data.get("accessToken") or "").strip() or str(data.get("refreshToken") or "").strip())


def _merge_cli_auth(path, key: str) -> dict[str, Any]:
    data = _read_object(path)
    data.pop("accessToken", None)
    data.pop("refreshToken", None)
    data["apiKey"] = key
    return data


def _merge_sdk_auth(path, key: str) -> dict[str, Any]:
    data = _read_object(path)
    if str(data.get("apiKey") or "") == key and data.get("version") == 1:
        return data
    return {
        "version": 1,
        "backendUrl": str(data.get("backendUrl") or CLI_BACKEND),
        "apiKey": key,
        "createdAtMs": int(time.time() * 1000),
    }


def _write_json(path, data: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(mode)
    tmp.replace(path)
    try:
        path.chmod(mode)
    except OSError:
        pass
