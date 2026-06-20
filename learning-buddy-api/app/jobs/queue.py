from __future__ import annotations

import logging

from app.clients.redis_client import enqueue_graph_run
from app.config import settings

logger = logging.getLogger(__name__)


def schedule_graph_run(
    student_id: str,
    homework_id: str,
    *,
    subject: str = "math",
    grade_band: str = "hs",
) -> None:
    if not settings.graph_enabled:
        return
    try:
        job_id = enqueue_graph_run(student_id, homework_id, subject, grade_band)
        logger.info("Enqueued graph job %s homework=%s", job_id, homework_id)
    except Exception:
        logger.exception("Failed to enqueue graph for homework=%s", homework_id)
