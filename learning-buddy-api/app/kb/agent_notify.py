"""Tell textbook-agent about knowledge-base import lifecycle events."""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _post_event(event: str, book_id: str, conversation_id: str = "") -> None:
    base = (settings.textbook_agent_url or "").strip().rstrip("/")
    sid = (book_id or "").strip()
    name = (event or "").strip()
    if not base or not sid or not name:
        return
    headers = {"Content-Type": "application/json"}
    token = (settings.textbook_agent_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = min(15.0, float(getattr(settings, "textbook_agent_timeout_s", 30) or 30))
    url = f"{base}/v1/tasks/emit"
    payload = {"book_id": sid}
    cid = (conversation_id or "").strip()
    if cid:
        payload["conversation_id"] = cid
    body = {"event": name, "book_id": sid, "payload": payload}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body, headers=headers)
            r.raise_for_status()
    except Exception:
        logger.warning("textbook-agent notify failed event=%s book=%s", name, sid, exc_info=True)


def notify_import_converting(book_id: str, conversation_id: str = "") -> None:
    _post_event("kb.import.converting", book_id, conversation_id)


def notify_import_completed(book_id: str, conversation_id: str = "") -> None:
    _post_event("kb.import.completed", book_id, conversation_id)
