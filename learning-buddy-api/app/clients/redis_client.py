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


def enqueue_import_job(job_id: str) -> str:
    payload = {"job_id": job_id, "type": "import"}
    get_redis().lpush(settings.import_queue, json.dumps(payload))
    return job_id


def enqueue_import35b_job(job_id: str) -> str:
    """Isolated 35B import queue. Uses a DEDICATED queue (``import35b_queue``),
    never the MinerU ``import_queue``, so the two lines share no Redis key."""
    payload = {"job_id": job_id, "type": "import35b"}
    get_redis().lpush(settings.import35b_queue, json.dumps(payload))
    return job_id


def purge_import_queue(job_id: str) -> int:
    """Remove pending MinerU import queue entries for ``job_id``. Returns items removed."""
    r = get_redis()
    removed = 0
    try:
        for raw in r.lrange(settings.import_queue, 0, -1):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("job_id") == job_id and payload.get("type") == "import":
                if r.lrem(settings.import_queue, 0, raw):
                    removed += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("purge import queue failed job=%s: %s", job_id, exc)
    return removed


def purge_import35b_queue(job_id: str) -> int:
    """Remove pending 35B queue entries for ``job_id``. Returns items removed."""
    r = get_redis()
    removed = 0
    try:
        for raw in r.lrange(settings.import35b_queue, 0, -1):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("job_id") == job_id and payload.get("type") == "import35b":
                if r.lrem(settings.import35b_queue, 0, raw):
                    removed += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("purge import35b queue failed job=%s: %s", job_id, exc)
    return removed


def enqueue_kb_import(book_id: str) -> str:
    payload = {"job_id": book_id, "type": "kb_import"}
    get_redis().lpush(settings.kb_import_queue, json.dumps(payload))
    return book_id


def purge_kb_import_queue(book_id: str) -> int:
    r = get_redis()
    removed = 0
    try:
        for raw in r.lrange(settings.kb_import_queue, 0, -1):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if payload.get("job_id") == book_id and payload.get("type") == "kb_import":
                if r.lrem(settings.kb_import_queue, 0, raw):
                    removed += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("purge kb import queue failed book=%s: %s", book_id, exc)
    return removed


KB_REVOKE_PREFIX = "lb:kb:revoked:"
KB_REVOKE_TTL_S = 7 * 86400


def revoke_kb_book(book_id: str) -> None:
    sid = (book_id or "").strip()
    if not sid:
        return
    try:
        get_redis().set(f"{KB_REVOKE_PREFIX}{sid}", "1", ex=KB_REVOKE_TTL_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning("revoke kb book failed book=%s: %s", sid, exc)


def clear_kb_revoke(book_id: str) -> None:
    sid = (book_id or "").strip()
    if not sid:
        return
    try:
        get_redis().delete(f"{KB_REVOKE_PREFIX}{sid}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("clear kb revoke failed book=%s: %s", sid, exc)


def is_kb_book_revoked(book_id: str) -> bool:
    sid = (book_id or "").strip()
    if not sid:
        return False
    try:
        return bool(get_redis().get(f"{KB_REVOKE_PREFIX}{sid}"))
    except Exception:
        return False


def enqueue_textbook_eval(request_id: str) -> str:
    payload = {"job_id": request_id, "type": "textbook_eval", "request_id": request_id}
    get_redis().lpush(settings.textbook_eval_queue, json.dumps(payload))
    return request_id


def enqueue_scout_run(run_id: str) -> str:
    payload = {"job_id": run_id, "type": "scout", "run_id": run_id}
    get_redis().lpush(settings.scout_queue, json.dumps(payload))
    return run_id


REINDEX_KEY_PREFIX = "lb:reindex:"


def set_reindex_status(job_id: str, data: dict) -> None:
    get_redis().set(f"{REINDEX_KEY_PREFIX}{job_id}", json.dumps(data), ex=86400)


def get_reindex_status(job_id: str) -> dict | None:
    raw = get_redis().get(f"{REINDEX_KEY_PREFIX}{job_id}")
    return json.loads(raw) if raw else None


def enqueue_reindex_job(job_id: str, node_ids: list[str]) -> str:
    payload = {"job_id": job_id, "type": "reindex", "node_ids": node_ids}
    get_redis().lpush(settings.publish_queue, json.dumps(payload))
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


# Studio import job live events (Redis pub/sub → SSE)
IMPORT_EVENTS_CHANNEL = "lb:events:import"


def publish_import_event(event: dict) -> None:
    """Publish a light-weight import progress event for Studio SSE fans."""
    try:
        payload = dict(event)
        payload.setdefault("ts", time.time())
        get_redis().publish(IMPORT_EVENTS_CHANNEL, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        logger.debug("publish_import_event failed: %s", exc)
