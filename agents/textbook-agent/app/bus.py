"""In-process event bus for /v1/events (jobs + config/surfaces/checkpoints)."""

from __future__ import annotations

import queue
import threading
from typing import Any

_lock = threading.Lock()
_subs: list[queue.Queue] = []

EVENT_TYPES = frozenset(
    {
        "job",
        "config",
        "surfaces",
        "hooks",
        "plugins",
        "prompts",
        "checkpoint",
        "reload",
        "page",
        "task",
        "import_meta",
    }
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


# Latest-wins for noisy ticks. One-shot types (import_meta, page, …) must stay.
_COALESCE_TYPES = frozenset(
    {"job", "config", "surfaces", "hooks", "plugins", "prompts"}
)


def _coalesce(q: queue.Queue, kind: str) -> None:
    kept: list[dict[str, Any]] = []
    while True:
        try:
            prev = q.get_nowait()
        except queue.Empty:
            break
        if str(prev.get("type") or "") != kind:
            kept.append(prev)
    for item in kept:
        try:
            q.put_nowait(item)
        except Exception:
            break


def _fanout(event: dict[str, Any]) -> None:
    dead: list[queue.Queue] = []
    with _lock:
        targets = list(_subs)
    kind = str(event.get("type") or "")
    for q in targets:
        try:
            if kind in _COALESCE_TYPES:
                _coalesce(q, kind)
            try:
                q.put_nowait(event)
            except queue.Full:
                _coalesce(q, kind if kind in _COALESCE_TYPES else "job")
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
