"""In-process event bus for /v1/events (jobs + config/surfaces/checkpoints)."""

from __future__ import annotations

import queue
import threading
from typing import Any

_lock = threading.Lock()
_subs: list[queue.Queue] = []

EVENT_TYPES = frozenset(
    {"job", "config", "surfaces", "hooks", "plugins", "prompts", "checkpoint", "reload"}
)


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=200)
    with _lock:
        _subs.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _lock:
        if q in _subs:
            _subs.remove(q)


def publish(kind: str, **fields: Any) -> dict[str, Any]:
    event = dict(fields)
    event["type"] = kind if kind in EVENT_TYPES else "config"
    _fanout(event)
    return event


def publish_dict(event: dict[str, Any]) -> None:
    _fanout(dict(event))


def _fanout(event: dict[str, Any]) -> None:
    dead: list[queue.Queue] = []
    with _lock:
        targets = list(_subs)
    for q in targets:
        try:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            q.put_nowait(event)
        except Exception:
            dead.append(q)
    if dead:
        with _lock:
            for q in dead:
                if q in _subs:
                    _subs.remove(q)


def reset_for_tests() -> None:
    with _lock:
        _subs.clear()
