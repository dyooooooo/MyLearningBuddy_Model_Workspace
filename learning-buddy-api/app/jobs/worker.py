from __future__ import annotations

import json
import logging
import time

import redis

from app.config import settings
from app.clients.redis_client import get_redis
from app.db.session import get_engine, get_session_factory
from app.graph.runner import run_graph_sync
from app.services.edge_worker import complete_edges_for_question

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


def main() -> None:
    get_engine()
    logger.info("buddy-worker started queues=%s,%s", settings.graph_queue, settings.edge_queue)
    r = get_redis()
    queues = [settings.graph_queue, settings.edge_queue]
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
            if queue_name == settings.graph_queue and payload.get("type") == "graph":
                _handle_graph(payload)
            elif queue_name == settings.edge_queue and payload.get("type") == "edges":
                _handle_edges(payload)
        except Exception:
            logger.exception("job failed queue=%s raw=%s", queue_name, raw)


if __name__ == "__main__":
    main()
