from __future__ import annotations

import threading
from contextvars import ContextVar

current_job_id: ContextVar[str] = ContextVar("current_job_id", default="")

_live_lock = threading.Lock()
_live: dict[str, str] = {"job": ""}


def bind_live_sdk_job(job_id: str) -> None:
    """Remember the in-flight Cursor job so tool callbacks on other threads see it."""
    with _live_lock:
        _live["job"] = (job_id or "").strip()


def live_sdk_job() -> str:
    return (current_job_id.get() or "").strip() or _live["job"]
