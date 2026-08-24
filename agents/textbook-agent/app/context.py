from __future__ import annotations

from contextvars import ContextVar

current_job_id: ContextVar[str] = ContextVar("current_job_id", default="")
