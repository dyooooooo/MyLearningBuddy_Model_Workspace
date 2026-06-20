"""Redis-backed companion session store + MinIO frame previews."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.clients.redis_client import get_redis
from app.config import settings
from app.schemas import CompanionSession, SessionEvent
from app.storage.minio_client import get_frame, put_frame

ISO = lambda: datetime.now(timezone.utc).isoformat()


def _session_key(session_id: str) -> str:
    return f"companion:session:{session_id}"


def new_session_id() -> str:
    return str(uuid.uuid4())


def new_page_id() -> str:
    return f"page-{uuid.uuid4().hex[:8]}"


def create_session(
    *,
    student_id: str,
    subject: str | None = None,
    note: str | None = None,
) -> CompanionSession:
    sid = new_session_id()
    now = ISO()
    session = CompanionSession(
        id=sid,
        status="active",
        started_at=now,
        current_page_id=new_page_id(),
        events=[SessionEvent(ts=now, type="SESSION_START", payload={"session_id": sid})],
        meta={
            "subject": subject or "math",
            "grade_band": "hs",
            "note": note,
            "student_id": student_id,
        },
    )
    save_session(session)
    return session


def load_session(session_id: str) -> CompanionSession:
    raw = get_redis().get(_session_key(session_id))
    if not raw:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return CompanionSession.model_validate(json.loads(raw))


def save_session(session: CompanionSession) -> None:
    get_redis().setex(
        _session_key(session.id),
        settings.session_ttl_seconds,
        json.dumps(session.model_dump(mode="json", by_alias=True), ensure_ascii=False),
    )


def save_frame_preview(
    student_id: str,
    session_id: str,
    frame_id: str,
    jpeg_bytes: bytes,
) -> None:
    put_frame(student_id, session_id, frame_id, jpeg_bytes)


def read_frame_preview_bytes(
    student_id: str,
    session_id: str,
    frame_id: str,
) -> bytes | None:
    return get_frame(student_id, session_id, frame_id)


def load_frame_preview(student_id: str, session_id: str, frame_id: str) -> bytes:
    session = load_session(session_id)
    if not any(f.id == frame_id for f in session.frames):
        raise HTTPException(status_code=404, detail=f"Frame not found: {frame_id}")
    data = read_frame_preview_bytes(student_id, session_id, frame_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Frame preview not found: {frame_id}")
    return data


def append_event(session: CompanionSession, event_type: str, payload: dict | None = None) -> None:
    session.events.append(SessionEvent(ts=ISO(), type=event_type, payload=payload or {}))
