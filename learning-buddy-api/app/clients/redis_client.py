from __future__ import annotations

import json
import logging
import time
import uuid

import redis

from app.config import settings

logger = logging.getLogger(__name__)
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        # socket_timeout=None — required for blocking BRPOP in buddy-worker
        _client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=None,
            socket_connect_timeout=10,
        )
    return _client


def redis_ok() -> bool:
    try:
        return get_redis().ping()
    except Exception:
        return False


def enqueue_edge_completion(node_id: str, subject: str, grade_band: str) -> str:
    job_id = str(uuid.uuid4())
    payload = {
        "job_id": job_id,
        "type": "edges",
        "node_id": node_id,
        "subject": subject,
        "grade_band": grade_band,
    }
    get_redis().lpush(settings.edge_queue, json.dumps(payload))
    return job_id


def enqueue_graph_run(student_id: str, homework_id: str, subject: str, grade_band: str) -> str:
    job_id = str(uuid.uuid4())
    payload = {
        "job_id": job_id,
        "type": "graph",
        "student_id": student_id,
        "homework_id": homework_id,
        "subject": subject,
        "grade_band": grade_band,
    }
    get_redis().lpush(settings.graph_queue, json.dumps(payload))
    return job_id


def acquire_vllm_slot(timeout_s: float | None = None) -> bool:
    timeout = timeout_s if timeout_s is not None else settings.vllm_acquire_timeout_s
    deadline = time.time() + timeout
    r = get_redis()
    key = "lb:vllm:semaphore"
    limit = settings.vllm_max_concurrent
    while time.time() < deadline:
        current = r.incr(key)
        if current == 1:
            r.expire(key, 600)
        if current <= limit:
            return True
        r.decr(key)
        time.sleep(0.25)
    return False


def release_vllm_slot() -> None:
    try:
        r = get_redis()
        val = r.decr("lb:vllm:semaphore")
        if val is not None and val < 0:
            r.set("lb:vllm:semaphore", 0)
    except Exception as e:
        logger.warning("release vllm slot failed: %s", e)
