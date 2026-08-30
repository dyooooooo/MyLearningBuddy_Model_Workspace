"""Process-wide Cursor client and the in-memory Agent dispatcher.

Official long-running pattern: launch one bridge, pass ``client=`` to every
``Agent.create`` / ``Agent.resume`` / ``Cursor.me`` / ``Agent.prompt``, keep
agent handles in a map, and close the client only on process exit or when no
sibling job is running. Closing the module default client while another
conversation is streaming is what surfaces as
``[canceled] This operation was aborted``.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client: Any = None
# conversation_id and sdk_id both point at the same handle when known.
_agents: dict[str, Any] = {}


def reset_for_tests() -> None:
    with _lock:
        _agents.clear()
    _set_client(None)


def install_client_for_tests(client: Any) -> None:
    """Tests install a fake client; ``ensure_client`` will not launch a bridge."""
    _set_client(client)


def _set_client(value: Any) -> None:
    global _client
    _client = value


def under_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def get_client() -> Any:
    """The live client, or None when tests have not installed one."""
    return _client


def ensure_client() -> Any:
    """Launch the workspace bridge once. No-op under pytest unless already set."""
    if _client is not None:
        return _client
    if under_pytest():
        return None
    with _lock:
        if _client is not None:
            return _client
        from cursor_sdk import CursorClient

        launched = CursorClient.launch_bridge(workspace=str(settings.home))
        _set_client(launched)
        logger.info("CursorClient.launch_bridge ready cwd=%s", settings.home)
        return launched


def close_owned_client() -> None:
    """Drop every cached Agent, then close our client and the default leftover."""
    drop_all_agents()
    with _lock:
        cli = _client
        _set_client(None)
    if cli is not None:
        closer = getattr(cli, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                logger.warning("owned CursorClient.close failed", exc_info=True)
    try:
        from app.bridge_cleanup import close_sdk_bridge

        close_sdk_bridge()
    except Exception:
        logger.warning("close_sdk_bridge after owned client failed", exc_info=True)


def remember_agent(*, conversation_id: str = "", sdk_id: str = "", agent: Any) -> None:
    if agent is None:
        return
    keys = [k for k in ((conversation_id or "").strip(), (sdk_id or "").strip()) if k]
    if not keys:
        return
    with _lock:
        for key in keys:
            _agents[key] = agent


def peek_agent(*, conversation_id: str = "", sdk_id: str = "") -> Any:
    with _lock:
        for key in ((conversation_id or "").strip(), (sdk_id or "").strip()):
            if key and key in _agents:
                return _agents[key]
    return None


def forget_keys(*keys: str) -> None:
    """Unmap keys without closing the Agent (another row may still own it)."""
    with _lock:
        for key in keys:
            if key:
                _agents.pop(key, None)


def drop_agent(*, conversation_id: str = "", sdk_id: str = "") -> None:
    """Forget and close a cached handle. Does not close the process client."""
    seeds = [k for k in ((conversation_id or "").strip(), (sdk_id or "").strip()) if k]
    if not seeds:
        return
    with _lock:
        wanted = {_agents[k] for k in seeds if k in _agents}
        if not wanted:
            return
        for key, agent in list(_agents.items()):
            if agent in wanted:
                _agents.pop(key, None)
        victims = list(wanted)
    for agent in victims:
        _close_agent(agent)


def drop_all_agents() -> None:
    with _lock:
        victims = list({id(a): a for a in _agents.values()}.values())
        _agents.clear()
    for agent in victims:
        _close_agent(agent)


def _close_agent(agent: Any) -> None:
    closer = getattr(agent, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception:
        logger.warning("agent.close failed", exc_info=True)


def cached_keys() -> list[str]:
    with _lock:
        return sorted(_agents)
