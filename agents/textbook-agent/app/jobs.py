from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from app.config import settings

SDK_KINDS = frozenset({"chat", "grade", "page_refine", "toc_extract", "printed_toc", "task"})

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_MAX_JOBS = 200
_loaded_home = ""
_cancel_ids: set[str] = set()
_runs: dict[str, Any] = {}


class JobCancelled(Exception):
    """User or startup stopped the job."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _traces_dir() -> Path:
    path = settings.traces_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _legacy_jsonl() -> Path:
    return settings.home / "logs" / "runs.jsonl"


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": row.get("job_id"),
        "kind": row.get("kind"),
        "status": row.get("status"),
        "payload": row.get("payload") or {},
        "result": row.get("result"),
        "error": row.get("error") or "",
        "trace": list(row.get("trace") or []),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _notify(row: dict[str, Any]) -> None:
    """Push the latest snapshot. Never drop a slow subscriber.

    A long Cursor run can emit hundreds of traces in seconds. The old
    ``put_nowait`` treated ``queue.Full`` as a dead client and unsubscribed
    it, so the dock spinner never saw ``completed``.
    """
    from app import bus

    snap = public_view(row)
    snap["type"] = "job"
    bus.publish_dict(snap)


def _persist(row: dict[str, Any]) -> None:
    job_id = str(row.get("job_id") or "").strip()
    if not job_id:
        return
    try:
        path = _traces_dir() / f"{job_id}.json"
        path.write_text(json.dumps(public_view(row), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _load() -> None:
    global _loaded_home
    home = str(settings.home)
    if _loaded_home == home:
        return
    _jobs.clear()
    _loaded_home = home
    latest: dict[str, dict[str, Any]] = {}
    _ingest_jsonl(latest)
    for path in sorted(_traces_dir().glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict) and item.get("job_id"):
            if is_rewound(item):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            latest[str(item["job_id"])] = item
    rows = sorted(latest.values(), key=lambda r: str(r.get("updated_at") or ""))
    for item in rows[-_MAX_JOBS:]:
        job_id = str(item["job_id"])
        if is_rewound(item):
            continue
        _jobs[job_id] = {
            "job_id": job_id,
            "kind": item.get("kind") or "grade",
            "status": item.get("status") or "completed",
            "payload": item.get("payload") or {},
            "result": item.get("result"),
            "error": item.get("error") or "",
            "trace": list(item.get("trace") or []),
            "created_at": item.get("created_at") or "",
            "updated_at": item.get("updated_at") or "",
        }


def _ingest_jsonl(latest: dict[str, dict[str, Any]]) -> None:
    path = _legacy_jsonl()
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not (isinstance(item, dict) and item.get("job_id")):
            continue
        if is_rewound(item):
            continue
        job_id = str(item["job_id"])
        latest[job_id] = item
        dest = _traces_dir() / f"{job_id}.json"
        if not dest.exists():
            try:
                dest.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError:
                pass


def create_job(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    _load()
    job_id = str(uuid.uuid4())
    stored = dict(payload or {})
    try:
        from app.logsetup import current_book, current_origin, current_trace, new_trace

        stored.setdefault("trace_id", current_trace() or new_trace())
        if current_book() and not stored.get("book_id"):
            stored["book_id"] = current_book()
        stored.setdefault("origin", current_origin() or "task")
    except Exception:
        pass
    row = {
        "job_id": job_id,
        "kind": kind,
        "status": "queued",
        "payload": stored,
        "result": None,
        "error": "",
        "trace": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        _jobs[job_id] = row
        _trim_locked()
        _persist(row)
        _notify(row)
    return dict(row)


def get_job(job_id: str) -> dict[str, Any] | None:
    _load()
    with _lock:
        row = _jobs.get(job_id)
        return dict(row) if row else None


def is_rewound(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return bool((payload or {}).get("rewound"))


def count_sdk_active(exclude: str = "") -> int:
    """How many Cursor sends are in flight (running), not counting ``exclude``."""
    skip = (exclude or "").strip()
    n = 0
    for row in list_jobs(200):
        if skip and str(row.get("job_id") or "") == skip:
            continue
        if str(row.get("kind") or "") not in SDK_KINDS:
            continue
        if str(row.get("status") or "") == "running":
            n += 1
    return n


def list_jobs(limit: int = 80) -> list[dict[str, Any]]:
    _load()
    with _lock:
        rows = [
            r
            for r in _jobs.values()
            if not is_rewound(r)
        ]
        rows = sorted(rows, key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        return [public_view(r) for r in rows[: max(1, min(limit, _MAX_JOBS))]]


def append_trace(job_id: str, kind: str, title: str, detail: str = "", persist: bool = False) -> None:
    step = {
        "t": _now(),
        "kind": kind,
        "title": (title or "")[:200],
        "detail": (detail or "")[:4000],
    }
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        row.setdefault("trace", []).append(step)
        row["updated_at"] = _now()
        _notify(row)
        if persist:
            _persist(row)


def bind_checkpoint(job_id: str, sha: str) -> None:
    sha = (sha or "").strip()
    if not sha:
        return
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        payload = dict(row.get("payload") or {})
        payload["checkpoint"] = sha
        row["payload"] = payload
        result = dict(row.get("result") or {})
        result["checkpoint"] = sha
        row["result"] = result
        row["updated_at"] = _now()
        _persist(row)
        _notify(row)


def detach_conversation(job_id: str) -> None:
    """Mark a turn discarded. Prefer ``delete_job`` after rewind."""
    _load()
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        payload = dict(row.get("payload") or {})
        payload["conversation_id"] = ""
        payload["rewound"] = True
        row["payload"] = payload
        result = dict(row.get("result") or {})
        if result.get("conversation_id"):
            result["conversation_id"] = ""
        row["result"] = result
        row["updated_at"] = _now()
        _persist(row)


def delete_job(job_id: str) -> bool:
    """Remove a discarded turn from memory and traces/."""
    job_id = (job_id or "").strip()
    if not job_id:
        return False
    _load()
    _cancel_ids.discard(job_id)
    _runs.pop(job_id, None)
    with _lock:
        existed = _jobs.pop(job_id, None) is not None
    path = _traces_dir() / f"{job_id}.json"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return existed


def purge_rewound() -> int:
    _load()
    with _lock:
        ids = [str(row.get("job_id") or "") for row in _jobs.values() if is_rewound(row)]
    n = 0
    for job_id in ids:
        if job_id and delete_job(job_id):
            n += 1
    return n


def job_checkpoint(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    return str((result or {}).get("checkpoint") or (payload or {}).get("checkpoint") or "").strip()


def job_plan_checkpoint(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    plan = result.get("plan") if isinstance((result or {}).get("plan"), dict) else {}
    return str((plan or {}).get("checkpoint") or "").strip()


def job_plan_id(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    plan = result.get("plan") if isinstance((result or {}).get("plan"), dict) else {}
    return str((plan or {}).get("id") or "").strip()


def reset_for_tests() -> None:
    global _loaded_home
    with _lock:
        _jobs.clear()
        _loaded_home = ""
        _cancel_ids.clear()
        _runs.clear()


def attach_run(job_id: str, run: Any) -> None:
    if job_id:
        _runs[job_id] = run


def detach_run(job_id: str) -> None:
    _runs.pop(job_id, None)


def is_cancelled(job_id: str) -> bool:
    if not job_id:
        return False
    if job_id in _cancel_ids:
        return True
    row = _jobs.get(job_id)
    return bool(row and row.get("status") == "cancelled")


def request_cancel(job_id: str, reason: str = "stopped") -> dict[str, Any] | None:
    job_id = (job_id or "").strip()
    if not job_id:
        return None
    _load()
    _cancel_ids.add(job_id)
    run = _runs.pop(job_id, None)
    if run is not None:
        try:
            cancel = getattr(run, "cancel", None)
            if callable(cancel):
                cancel()
        except Exception:
            pass
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return None
        if row.get("status") in ("completed", "failed"):
            return public_view(row)
        row["status"] = "cancelled"
        row["error"] = (reason or "stopped")[:2000]
        row["updated_at"] = _now()
        _persist(row)
        _notify(row)
        return public_view(row)


def cancel_active(reason: str = "interrupted") -> int:
    n = 0
    for row in list_jobs(200):
        if row.get("status") in ("queued", "running"):
            request_cancel(str(row.get("job_id") or ""), reason)
            n += 1
    return n


def mark_approval(job_id: str, record: dict[str, Any]) -> None:
    """Attach a grant/refuse/revise decision to the job so the card updates live."""
    job_id = (job_id or "").strip()
    if not job_id or not isinstance(record, dict):
        return
    req_id = str(record.get("req_id") or "").strip()
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        result = dict(row.get("result") or {})
        result["approval"] = record
        row["result"] = result
        for step in row.get("trace") or []:
            if not isinstance(step, dict) or step.get("kind") != "approval":
                continue
            try:
                detail = json.loads(step.get("detail") or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(detail, dict):
                continue
            if req_id and str(detail.get("req_id") or "") != req_id:
                continue
            detail.update(record)
            step["detail"] = json.dumps(detail, ensure_ascii=False)[:4000]
        row["updated_at"] = _now()
        _persist(row)
        _notify(row)


def patch_result(job_id: str, persist: bool = False, **fields: Any) -> None:
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        result = dict(row.get("result") or {})
        result.update(fields)
        row["result"] = result
        row["updated_at"] = _now()
        _notify(row)
        if persist:
            _persist(row)


def _set(job_id: str, persist: bool = False, **fields: Any) -> None:
    with _lock:
        row = _jobs.get(job_id)
        if row is None:
            return
        row.update(fields)
        row["updated_at"] = _now()
        _notify(row)
        if persist:
            _persist(row)


def _trim_locked() -> None:
    if len(_jobs) <= _MAX_JOBS:
        return
    oldest = sorted(_jobs.values(), key=lambda r: str(r.get("updated_at") or ""))
    for row in oldest[: len(_jobs) - _MAX_JOBS]:
        _jobs.pop(str(row.get("job_id")), None)


def subscribe() -> queue.Queue:
    from app import bus

    return bus.subscribe()


def unsubscribe(q: queue.Queue) -> None:
    from app import bus

    bus.unsubscribe(q)


def _kick_auto_resume(job_id: str) -> None:
    try:
        from app.auto_resume import kick_once

        kick_once(job_id)
    except Exception:
        logger.warning("auto resume kick failed job=%s", job_id, exc_info=True)


def _flush_working(job_id: str) -> None:
    try:
        from app import working_memory

        row = get_job(job_id)
        if row:
            working_memory.write_from_job(row)
    except Exception:
        logger.warning("working memory flush failed job=%s", job_id, exc_info=True)


def _emit_task_completed(job_id: str) -> None:
    row = get_job(job_id)
    if not row or row.get("kind") not in {"chat", "printed_toc"}:
        return
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    task_id = str((payload or {}).get("task_id") or "").strip()
    if not task_id:
        return
    trigger = str((payload or {}).get("trigger") or "").strip()
    try:
        from app import tasks

        task = tasks.get_task(task_id)
        if not task:
            return
        if trigger != str(task.get("trigger") or ""):
            return
        extra = {
            "book_id": str((payload or {}).get("book_id") or ""),
            "conversation_id": str((payload or {}).get("conversation_id") or ""),
            "chat_job_id": job_id,
        }
        tasks.emit(f"task.{task_id}.completed", extra)
    except Exception:
        logger.warning("task completed emit failed job=%s", job_id, exc_info=True)


def _keep_live_result_fields(job_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """``_set(..., result=)`` replaces the whole blob; keep fields written mid-run."""
    prior = get_job(job_id) or {}
    prior_result = prior.get("result") if isinstance(prior.get("result"), dict) else {}
    out = dict(result)
    sha = job_checkpoint(prior)
    if sha and not str(out.get("checkpoint") or "").strip():
        out["checkpoint"] = sha
    for key in ("agent_id", "run_id"):
        if not str(out.get(key) or "").strip() and str(prior_result.get(key) or "").strip():
            out[key] = prior_result[key]
    if "resumed" not in out and "resumed" in prior_result:
        out["resumed"] = prior_result["resumed"]
    return out


def start_background(job_id: str, fn: Callable[[], dict[str, Any]]) -> None:
    row = get_job(job_id) or {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    trace = str((payload or {}).get("trace_id") or "")
    book = str((payload or {}).get("book_id") or "")
    origin = str((payload or {}).get("origin") or "task")

    def _run() -> None:
        from app.logsetup import bind

        with bind(trace_id=trace, book_id=book, origin=origin or "task"):
            _run_job()

    def _run_job() -> None:
        if is_cancelled(job_id):
            return
        _set(job_id, persist=True, status="running")
        try:
            result = fn()
            if is_cancelled(job_id):
                _flush_working(job_id)
                return
            if isinstance(result, dict):
                result = _keep_live_result_fields(job_id, result)
            _set(job_id, persist=True, status="completed", result=result)
            _flush_working(job_id)
            _emit_task_completed(job_id)
        except JobCancelled:
            request_cancel(job_id, "stopped")
            _flush_working(job_id)
        except Exception as exc:
            if is_cancelled(job_id):
                _flush_working(job_id)
                return
            try:
                from app.sdk_errors import humanize_sdk_error

                err = humanize_sdk_error(exc)
            except Exception:
                err = str(exc)
            _set(job_id, persist=True, status="failed", error=err[:2000])
            _flush_working(job_id)
            _kick_auto_resume(job_id)
        finally:
            detach_run(job_id)

    threading.Thread(target=_run, name=f"agent-job-{job_id[:8]}", daemon=True).start()
