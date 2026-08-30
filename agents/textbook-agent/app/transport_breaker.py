"""Stop resuming a Cursor thread whose transport keeps dying.

Cursor's guidance for the SDK is explicit: once the stream path stays dead,
further ``send()`` calls on the same agent will not revive it, so a consumer
should cap consecutive transport failures and rebuild instead. This is that cap.

Counting is per Cursor agent id and only for transport-class failures — a hook
denial or a genuine rejected key must never burn a strike, because starting a
fresh thread would not help and would silently lose the conversation.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Cursor suggests 3-5. Three keeps at most two doomed resumes in front of the
# user before we hand them a working thread.
LIMIT = 3

_lock = threading.Lock()
_strikes: dict[str, int] = {}


def note_failure(agent_id: str) -> int:
    """Record one transport failure against ``agent_id``; return the new count."""
    key = (agent_id or "").strip()
    if not key:
        return 0
    with _lock:
        count = _strikes.get(key, 0) + 1
        _strikes[key] = count
    logger.info("transport strike %s/%s for agent %s", count, LIMIT, key)
    return count


def trip(agent_id: str) -> None:
    """Retire ``agent_id`` on the spot, without waiting for ``LIMIT`` strikes.

    For failures the SDK has already retried internally there is nothing left to
    probe: it told us the thread stopped advancing, so one report is the whole
    evidence. Counting to three here would just buy two more turns that hang
    until the eval timeout.
    """
    key = (agent_id or "").strip()
    if not key:
        return
    with _lock:
        _strikes[key] = max(_strikes.get(key, 0), LIMIT)
    logger.info("transport breaker tripped outright for agent %s", key)


def note_success(agent_id: str) -> None:
    """A completed turn clears the record; only *consecutive* failures count."""
    key = (agent_id or "").strip()
    if not key:
        return
    with _lock:
        _strikes.pop(key, None)


def is_tripped(agent_id: str) -> bool:
    key = (agent_id or "").strip()
    if not key:
        return False
    with _lock:
        return _strikes.get(key, 0) >= LIMIT


def reset(agent_id: str = "") -> None:
    """Forget ``agent_id``, or everything when called bare (tests, restarts)."""
    key = (agent_id or "").strip()
    with _lock:
        if key:
            _strikes.pop(key, None)
        else:
            _strikes.clear()
