from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

import redis

from app.config import settings
from app.clients.redis_client import get_redis
from app.db.session import get_engine, get_session_factory
from app.graph.runner import run_graph_sync
from app.services.corpus_import import process_import_job
from app.services.corpus_admin import run_reindex_job
from app.services.edge_worker import complete_edges_for_question
from app.services.scout_agent import run_scout_sync

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def _handle_graph(payload: dict) -> None:
    run_graph_sync(
        payload["student_id"],
        payload["homework_id"],
        subject=payload.get("subject", "math"),
        grade_band=payload.get("grade_band", "hs"),
    )


def _handle_edges(payload: dict) -> None:
    factory = get_session_factory()
    with factory() as session:
        n = complete_edges_for_question(
            session,
            node_id=payload["node_id"],
            subject=payload.get("subject", "math"),
            grade_band=payload.get("grade_band", "hs"),
        )
        session.commit()
        logger.info("edge completion node=%s edges=%s", payload.get("node_id"), n)


def _handle_import(payload: dict) -> None:
    from app.services import pipeline_telemetry
    from app.services.pipeline_telemetry import ImportCanceled

    from app.db.models import CorpusImportJob

    job_id = payload.get("job_id", "")
    logger.info("import worker dequeue id=%s", job_id)
    factory = get_session_factory()

    # A job canceled (or deleted) while still queued must not be resurrected.
    with factory() as session:
        existing = session.get(CorpusImportJob, job_id)
        if existing is None:
            logger.info("import worker skip — job gone id=%s", job_id)
            return
        if existing.status in ("canceled", "failed", "chunked"):
            logger.info(
                "import worker skip — terminal status=%s id=%s",
                existing.status,
                job_id,
            )
            return

    beat = pipeline_telemetry.current_beat()
    if beat is not None:
        beat.set_active(job_id, payload.get("label", ""))
    # Do NOT clear_cancel here. A cancel/delete while the job was queued (or a
    # racing delete) must still abort this run. Job IDs are UUIDs so stale flags
    # from a prior job cannot collide. Worker clears the flag in ``finally``.
    if pipeline_telemetry.is_canceled(job_id):
        logger.info("import worker abort — cancel flag set before start id=%s", job_id)
        _mark_import_job_failed(job_id, "已取消（用户删除或停止）", status="canceled")
        if beat is not None:
            beat.set_idle()
        return

    max_attempts = 4
    try:
        for attempt in range(max_attempts):
            if attempt > 0:
                delay = 1.5 * attempt
                logger.info("import worker retry id=%s attempt=%d delay=%.1fs", job_id, attempt + 1, delay)
                time.sleep(delay)
                if pipeline_telemetry.is_canceled(job_id):
                    logger.info("import worker abort — canceled during retry id=%s", job_id)
                    _mark_import_job_failed(job_id, "已取消（用户删除或停止）", status="canceled")
                    return
            with factory() as session:
                try:
                    job = process_import_job(session, job_id)
                    session.commit()
                    logger.info(
                        "import worker done id=%s status=%s textbook=%s",
                        job_id,
                        job.status,
                        job.textbook_node_id or "",
                    )
                    return
                except ImportCanceled:
                    session.rollback()
                    logger.info("import worker canceled id=%s", job_id)
                    _mark_import_job_failed(job_id, "已取消（用户删除或停止）", status="canceled")
                    return
                except ValueError as exc:
                    session.rollback()
                    if "import job not found" in str(exc):
                        logger.info("import worker stop — job gone id=%s", job_id)
                        return
                    logger.exception("import worker failed id=%s", job_id)
                    _mark_import_job_failed(job_id, str(exc))
                    return
                except Exception as exc:
                    session.rollback()
                    logger.exception("import worker failed id=%s", job_id)
                    _mark_import_job_failed(job_id, str(exc))
                    return
    finally:
        pipeline_telemetry.clear_cancel(job_id)
        pipeline_telemetry.clear_job_live(job_id)
        if beat is not None:
            beat.set_idle()


def _mark_import_job_failed(job_id: str, error: str, *, status: str = "failed") -> None:
    from app.db.models import CorpusImportJob

    factory = get_session_factory()
    with factory() as session:
        job = session.get(CorpusImportJob, job_id)
        if job is None:
            return
        job.status = status
        job.error = (error or "unknown error")[:4000]
        job.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()


def _handle_import35b(payload: dict) -> None:
    """Isolated 35B import handler — never touches process_import_job (MinerU)."""
    from app.import_35b import service

    job_id = payload.get("job_id", "")
    logger.info("import35b worker dequeue id=%s", job_id)
    service.run_convert(job_id)


def _handle_kb_import(payload: dict) -> None:
    from app.kb.import_service import mark_failed, process_kb_book

    book_id = payload.get("job_id", "")
    logger.info("kb import worker dequeue id=%s", book_id)
    try:
        book = process_kb_book(book_id)
        logger.info("kb import done id=%s status=%s pages=%s", book.get("id"), book.get("status"), book.get("page_count"))
    except Exception as exc:
        logger.exception("kb import failed id=%s", book_id)
        mark_failed(book_id, str(exc))


def _handle_textbook_eval(payload: dict) -> None:
    from app.services.textbook_eval import call_textbook_agent, mark_eval_failed

    request_id = payload.get("request_id") or payload.get("job_id", "")
    logger.info("textbook eval dequeue request_id=%s", request_id)
    factory = get_session_factory()
    with factory() as session:
        try:
            call_textbook_agent(session, request_id)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.exception("textbook agent failed request_id=%s", request_id)
            with factory() as s2:
                mark_eval_failed(s2, request_id, str(exc))
                s2.commit()


def _handle_scout(payload: dict) -> None:
    run_id = payload.get("run_id") or payload.get("job_id", "")
    factory = get_session_factory()
    with factory() as session:
        try:
            run_scout_sync(session, run_id)
            session.commit()
            logger.info("scout run done id=%s", run_id)
        except Exception:
            session.rollback()
            logger.exception("scout run failed id=%s", run_id)


def _handle_reindex(payload: dict) -> None:
    job_id = payload.get("job_id", "")
    node_ids = payload.get("node_ids") or []
    logger.info("reindex job start id=%s nodes=%s", job_id, len(node_ids))
    run_reindex_job(node_ids, job_id)
    logger.info("reindex job done id=%s", job_id)


def main() -> None:
    get_engine()
    from app.services import pipeline_telemetry

    beat = pipeline_telemetry.init_worker_beat()
    logger.info("pipeline telemetry worker_id=%s", beat.worker_id)

    # Re-enqueue import jobs orphaned by a prior worker crash/restart.
    try:
        factory = get_session_factory()
        with factory() as session:
            from app.services.import_recovery import recover_orphaned_import_jobs

            out = recover_orphaned_import_jobs(session)
            if out["requeued_count"]:
                logger.info(
                    "orphan import recovery: requeued=%d ids=%s",
                    out["requeued_count"],
                    out["requeued"],
                )
    except Exception:
        logger.exception("orphan import recovery failed")

    queues = [
        settings.graph_queue,
        settings.edge_queue,
        settings.import_queue,
        settings.import35b_queue,
        settings.kb_import_queue,
        settings.textbook_eval_queue,
        settings.scout_queue,
        settings.publish_queue,
    ]
    logger.info("buddy-worker started queues=%s", ",".join(queues))
    r = get_redis()
    while True:
        try:
            item = r.brpop(queues, timeout=5)
        except (redis.TimeoutError, TimeoutError, OSError) as e:
            logger.debug("queue poll: %s", e)
            continue
        if not item:
            continue
        queue_name, raw = item
        try:
            payload = json.loads(raw)
            job_type = payload.get("type", "")
            if queue_name == settings.graph_queue and job_type == "graph":
                _handle_graph(payload)
            elif queue_name == settings.edge_queue and job_type == "edges":
                _handle_edges(payload)
            elif queue_name == settings.import_queue and job_type == "import":
                _handle_import(payload)
            elif queue_name == settings.import35b_queue and job_type == "import35b":
                _handle_import35b(payload)
            elif queue_name == settings.kb_import_queue and job_type == "kb_import":
                _handle_kb_import(payload)
            elif queue_name == settings.textbook_eval_queue and job_type == "textbook_eval":
                _handle_textbook_eval(payload)
            elif queue_name == settings.scout_queue and job_type == "scout":
                _handle_scout(payload)
            elif queue_name == settings.publish_queue and job_type == "reindex":
                _handle_reindex(payload)
        except Exception:
            logger.exception("job failed queue=%s raw=%s", queue_name, raw)


if __name__ == "__main__":
    main()
