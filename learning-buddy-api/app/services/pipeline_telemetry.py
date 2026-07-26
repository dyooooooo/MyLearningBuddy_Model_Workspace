"""Pipeline telemetry — honest, real-time view of import worker activity.

This is NOT a microservice per pipeline step. The import pipeline is a single
orchestrated task running inside buddy-worker. This module is a thin telemetry
layer (Redis-backed, ephemeral) that the orchestrator *writes* and the Studio
topology *reads*:

  - Worker heartbeats: how many worker processes are alive and what each is doing
    (key ``lb:pipeline:worker:<wid>`` hash with TTL — auto-expires if worker dies).
  - Per-job live phase: current node + sub-progress + last error
    (key ``lb:pipeline:job:<job_id>``).
  - Cooperative cancel flag: ``lb:pipeline:cancel:<job_id>`` — the worker checks
    this between pages and aborts gracefully so a textbook can be deleted without
    a Postgres row-lock deadlock.

All functions are no-op safe if Redis is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WORKER_KEY_PREFIX = "lb:pipeline:worker:"
JOB_KEY_PREFIX = "lb:pipeline:job:"
CANCEL_KEY_PREFIX = "lb:pipeline:cancel:"

WORKER_TTL_SECONDS = 20
HEARTBEAT_INTERVAL_SECONDS = 5
JOB_LIVE_TTL_SECONDS = 3600
CANCEL_TTL_SECONDS = 3600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redis():
    try:
        from app.clients.redis_client import get_redis

        return get_redis()
    except Exception:  # pragma: no cover - redis optional
        return None


class WorkerBeat:
    """Mutable current-activity state for one worker process.

    A daemon thread flushes this to Redis every few seconds with a TTL so the
    worker's liveness is self-expiring (no stale 'ghost' workers if it crashes).
    """

    def __init__(self, worker_id: str) -> None:
        self.worker_id = worker_id
        self._lock = threading.Lock()
        self._state: dict = {
            "worker_id": worker_id,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "status": "idle",          # idle | busy
            "job_id": "",
            "label": "",
            "phase": "",
            "detail": "",
            "page": 0,
            "total": 0,
            "error": "",
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._heartbeat_loop, name="pipeline-heartbeat", daemon=True
        )
        self._thread.start()
        self._flush()  # immediate first beat
        logger.info("pipeline heartbeat started worker=%s", self.worker_id)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            self._flush()

    def _flush(self) -> None:
        r = _redis()
        if r is None:
            return
        with self._lock:
            payload = dict(self._state)
        payload["updated_at"] = _now_iso()
        try:
            key = f"{WORKER_KEY_PREFIX}{self.worker_id}"
            r.set(key, json.dumps(payload), ex=WORKER_TTL_SECONDS)
        except Exception as e:  # pragma: no cover
            logger.debug("heartbeat flush failed: %s", e)

    # -- activity updates --------------------------------------------------
    def set_active(self, job_id: str, label: str = "") -> None:
        with self._lock:
            self._state.update(
                status="busy", job_id=job_id, label=label,
                phase="", detail="", page=0, total=0, error="",
                started_at=_now_iso(),
            )
        self._flush()

    def update_phase(
        self, phase: str, detail: str = "", *,
        page: int = 0, total: int = 0, error: str = "",
    ) -> None:
        with self._lock:
            self._state.update(phase=phase, detail=detail, page=page, total=total)
            if error:
                self._state["error"] = error
        self._flush()
        # Mirror to the per-job live key so the UI can read job-centric state too.
        if self._state.get("job_id"):
            set_job_live(
                self._state["job_id"],
                {
                    "phase": phase, "detail": detail, "page": page,
                    "total": total, "error": error or "",
                    "worker_id": self.worker_id, "updated_at": _now_iso(),
                },
            )

    def set_idle(self) -> None:
        with self._lock:
            self._state.update(
                status="idle", job_id="", label="", phase="",
                detail="", page=0, total=0, error="",
            )
        self._flush()


# Module-global current worker beat (set by worker main). No-op if unset.
_current_beat: WorkerBeat | None = None


def init_worker_beat() -> WorkerBeat:
    global _current_beat
    if _current_beat is None:
        wid = os.environ.get("PIPELINE_WORKER_ID") or f"w-{socket.gethostname()}-{os.getpid()}"
        _current_beat = WorkerBeat(wid)
        _current_beat.start()
    return _current_beat


def current_beat() -> WorkerBeat | None:
    return _current_beat


def update_phase(
    phase: str, detail: str = "", *, page: int = 0, total: int = 0, error: str = ""
) -> None:
    """Convenience used by orchestration code; no-op outside worker."""
    if _current_beat is not None:
        _current_beat.update_phase(phase, detail, page=page, total=total, error=error)


# -- per-job live state (readable by API) ----------------------------------
def set_job_live(job_id: str, data: dict) -> None:
    r = _redis()
    if r is None or not job_id:
        return
    try:
        r.set(f"{JOB_KEY_PREFIX}{job_id}", json.dumps(data), ex=JOB_LIVE_TTL_SECONDS)
    except Exception as e:  # pragma: no cover
        logger.debug("set_job_live failed: %s", e)


def get_job_live(job_id: str) -> dict | None:
    r = _redis()
    if r is None or not job_id:
        return None
    try:
        raw = r.get(f"{JOB_KEY_PREFIX}{job_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def clear_job_live(job_id: str) -> None:
    r = _redis()
    if r is None or not job_id:
        return
    try:
        r.delete(f"{JOB_KEY_PREFIX}{job_id}")
    except Exception:
        pass


def list_workers() -> list[dict]:
    r = _redis()
    if r is None:
        return []
    out: list[dict] = []
    try:
        for key in r.scan_iter(match=f"{WORKER_KEY_PREFIX}*", count=50):
            raw = r.get(key)
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except Exception:
                continue
    except Exception as e:  # pragma: no cover
        logger.debug("list_workers failed: %s", e)
    out.sort(key=lambda w: w.get("worker_id", ""))
    return out


# -- cooperative cancel -----------------------------------------------------
def request_cancel(job_id: str) -> None:
    r = _redis()
    if r is None or not job_id:
        return
    try:
        r.set(f"{CANCEL_KEY_PREFIX}{job_id}", "1", ex=CANCEL_TTL_SECONDS)
        logger.info("pipeline cancel requested job=%s", job_id)
    except Exception as e:  # pragma: no cover
        logger.debug("request_cancel failed: %s", e)


def is_canceled(job_id: str) -> bool:
    r = _redis()
    if r is None or not job_id:
        return False
    try:
        return bool(r.get(f"{CANCEL_KEY_PREFIX}{job_id}"))
    except Exception:
        return False


def clear_cancel(job_id: str) -> None:
    r = _redis()
    if r is None or not job_id:
        return
    try:
        r.delete(f"{CANCEL_KEY_PREFIX}{job_id}")
    except Exception:
        pass


class ImportCanceled(RuntimeError):
    """Raised inside the worker when a cooperative cancel is detected."""


def raise_if_canceled(job_id: str, *, verify_corpus_job: bool = False) -> None:
    """Abort if Redis cancel flag is set.

    When ``verify_corpus_job`` is True (MinerU corpus import only), also abort if
    the ``CorpusImportJob`` row is gone or already ``canceled``. Do not use this
    flag for 35B jobs — they live in a different table.
    """
    if is_canceled(job_id):
        raise ImportCanceled(f"import job canceled: {job_id}")
    if not verify_corpus_job or not job_id:
        return
    try:
        from app.db.models import CorpusImportJob
        from app.db.session import get_session_factory

        with get_session_factory() as session:
            job = session.get(CorpusImportJob, job_id)
            if job is None:
                raise ImportCanceled(f"import job deleted: {job_id}")
            if job.status == "canceled":
                raise ImportCanceled(f"import job canceled: {job_id}")
    except ImportCanceled:
        raise
    except Exception as e:  # pragma: no cover — DB blip must not kill a healthy run
        logger.debug("raise_if_canceled db check skipped: %s", e)
