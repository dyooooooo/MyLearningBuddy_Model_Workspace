"""Recover import jobs orphaned by worker restarts or crashes."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.clients.redis_client import enqueue_import_job
from app.db.models import CorpusImportJob
from app.services.corpus_admin import ACTIVE_IMPORT_STATUSES

logger = logging.getLogger(__name__)

# Jobs with no meta update for this long are considered orphaned.
STALE_SECONDS = 180


def parse_job_updated_at(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def job_is_stale(
    job: CorpusImportJob,
    workers: list[dict],
    *,
    stale_seconds: int = STALE_SECONDS,
) -> tuple[bool, int]:
    """True when an active job has no worker working on it and has not updated recently."""
    busy_on_job = any(
        w.get("status") == "busy" and w.get("job_id") == job.id for w in workers
    )
    if busy_on_job:
        return False, 0
    updated = parse_job_updated_at(job.updated_at)
    if updated is None:
        return True, stale_seconds + 1
    age_s = int((datetime.now(timezone.utc) - updated).total_seconds())
    return age_s >= stale_seconds, age_s


def recover_orphaned_import_jobs(
    session: Session,
    *,
    stale_seconds: int = STALE_SECONDS,
) -> dict:
    """Re-enqueue in-flight import jobs left behind after a worker restart.

    Only jobs whose ``updated_at`` is older than ``stale_seconds`` are
    recovered — avoids racing with a job that is actively running on another
    worker replica (future-proof; single worker today).

    ``uploaded`` jobs (never picked up) are always re-enqueued regardless of age.
    """
    from app.services import pipeline_telemetry

    now = datetime.now(timezone.utc)
    rows = (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.status.in_(ACTIVE_IMPORT_STATUSES))
        .order_by(CorpusImportJob.created_at.asc())
        .all()
    )

    requeued: list[str] = []
    skipped_fresh: list[str] = []

    for job in rows:
        if pipeline_telemetry.is_canceled(job.id):
            continue

        updated = parse_job_updated_at(job.updated_at)
        age_s = (now - updated).total_seconds() if updated else stale_seconds + 1

        if job.status != "uploaded" and age_s < stale_seconds:
            skipped_fresh.append(job.id)
            continue

        enqueue_import_job(job.id)
        requeued.append(job.id)
        logger.info(
            "orphan import re-enqueued id=%s status=%s age_s=%.0f label=%r",
            job.id,
            job.status,
            age_s,
            job.label,
        )

    return {
        "requeued": requeued,
        "requeued_count": len(requeued),
        "skipped_fresh": skipped_fresh,
    }
