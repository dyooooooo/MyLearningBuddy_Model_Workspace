"""On-disk logging for textbook-agent. Files live under AGENT_HOME/logs/."""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from app.config import settings

LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_BACKUP_COUNT = 10
PAYLOAD_KEEP_DAYS = 30
HOOKS_MAX_BYTES = 20 * 1024 * 1024
CLEANUP_INTERVAL_S = 24 * 3600

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("agent_trace_id", default="")
_book_id: contextvars.ContextVar[str] = contextvars.ContextVar("agent_book_id", default="")
_origin: contextvars.ContextVar[str] = contextvars.ContextVar("agent_origin", default="")

_configured = False
_last_cleanup = 0.0
_events = logging.getLogger("agent.events")


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = current_trace() or "-"
        record.book_id = current_book() or "-"
        record.origin = current_origin() or "-"
        return True


def logs_dir() -> Path:
    path = settings.home / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def current_trace() -> str:
    return _trace_id.get() or ""


def current_book() -> str:
    return _book_id.get() or ""


def current_origin() -> str:
    return _origin.get() or ""


def new_trace() -> str:
    return uuid.uuid4().hex[:16]


@contextmanager
def bind(*, trace_id: str = "", book_id: str = "", origin: str = "") -> Iterator[None]:
    tokens = []
    if trace_id:
        tokens.append(_trace_id.set(trace_id))
    if book_id:
        tokens.append(_book_id.set(book_id))
    if origin:
        tokens.append(_origin.set(origin))
    try:
        yield
    finally:
        for token in reversed(tokens):
            token.var.reset(token)


def digest_text(text: str, *, limit: int = 120) -> dict[str, Any]:
    raw = text or ""
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return {
        "chars": len(raw),
        "lines": raw.count("\n") + (1 if raw and not raw.endswith("\n") else 0),
        "sha": sha,
        "head": raw[:limit].replace("\n", "\\n"),
    }


def digest_obj(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        if len(value) <= 160:
            return value
        return digest_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:24]:
            out[str(key)] = digest_obj(item)
        if len(value) > 24:
            out["_more"] = len(value) - 24
        return out
    if isinstance(value, list):
        return {"len": len(value), "head": [digest_obj(v) for v in value[:8]]}
    return digest_text(str(value))


def setup_logging() -> dict[str, Any]:
    """Attach rotating file handlers. Never block process startup."""
    global _configured
    if _configured:
        return {"ok": True, "already": True}
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(trace_id)s book=%(book_id)s origin=%(origin)s] "
        "%(name)s: %(message)s"
    )
    filt = _ContextFilter()
    root = logging.getLogger()
    if not root.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        stream.addFilter(filt)
        root.addHandler(stream)
        root.setLevel(logging.INFO)
    else:
        for handler in root.handlers:
            handler.addFilter(filt)
            if not handler.formatter:
                handler.setFormatter(fmt)
    info: dict[str, Any] = {"ok": True, "dir": str(logs_dir())}
    try:
        dest = logs_dir()
        probe = dest / ".write-probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        file_handler = RotatingFileHandler(
            dest / "agent.log",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        file_handler.addFilter(filt)
        root.addHandler(file_handler)
        event_handler = RotatingFileHandler(
            dest / "events.jsonl",
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        event_handler.setFormatter(logging.Formatter("%(message)s"))
        _events.handlers.clear()
        _events.addHandler(event_handler)
        _events.setLevel(logging.INFO)
        _events.propagate = False
    except OSError as exc:
        info = {"ok": False, "error": str(exc)[:200]}
        logging.getLogger(__name__).warning("log dir not writable, stdout only: %s", exc)
    _configured = True
    return info


def event(kind: str, **fields: Any) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "trace_id": current_trace(),
        "book_id": current_book() or str(fields.get("book_id") or ""),
        "origin": current_origin(),
        **fields,
    }
    try:
        _events.info(json.dumps(row, ensure_ascii=False, default=str))
    except Exception:
        logging.getLogger(__name__).warning("event write failed kind=%s", kind, exc_info=True)


def dump_payload(name: str, text: str) -> str:
    """Write full text for a failed/audit case. Returns relative path or empty."""
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name or "payload"))[:80]
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    folder = logs_dir() / "payloads" / day / (current_trace() or "notrace")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / safe
        path.write_text(text if text is not None else "", encoding="utf-8")
        return str(path.relative_to(settings.home))
    except OSError:
        logging.getLogger(__name__).warning("dump_payload failed name=%s", name, exc_info=True)
        return ""


def maybe_cleanup(now: float | None = None) -> dict[str, int]:
    """Daily cleanup of payloads/audit/traces and oversized hooks.log."""
    global _last_cleanup
    ts = now if now is not None else time.time()
    if ts - _last_cleanup < CLEANUP_INTERVAL_S:
        return {"skipped": 1}
    _last_cleanup = ts
    removed = 0
    cutoff = ts - PAYLOAD_KEEP_DAYS * 86400
    for folder in (
        logs_dir() / "payloads",
        logs_dir() / "audit",
        settings.traces_dir,
    ):
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    hooks = logs_dir() / "hooks.log"
    rotated = 0
    if hooks.is_file():
        try:
            if hooks.stat().st_size > HOOKS_MAX_BYTES:
                bak = logs_dir() / "hooks.log.1"
                if bak.exists():
                    bak.unlink()
                os.replace(hooks, bak)
                rotated = 1
        except OSError:
            pass
    event("log.cleanup", removed=removed, hooks_rotated=rotated)
    return {"removed": removed, "hooks_rotated": rotated}
