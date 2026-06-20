"""Session helpers shared by service and pipeline (avoid circular imports)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.buddy_client import emit_handwriting_growth, ingest_homework
from app.companion import store
from app.companion.question_timing import estimate_question_timings
from app.companion.question_frame_map import build_question_frame_map
from app.schemas import CompanionSession, HomeworkEntry

logger = logging.getLogger(__name__)

ISO = lambda: datetime.now(timezone.utc).isoformat()


def student_id(session: CompanionSession) -> str:
    sid = (session.meta or {}).get("student_id") or ""
    if not sid:
        raise HTTPException(status_code=400, detail="Session missing student_id in meta")
    return str(sid)


def subject_grade(session: CompanionSession) -> tuple[str, str]:
    meta = session.meta or {}
    return str(meta.get("subject") or "math"), str(meta.get("grade_band") or "hs")


def page_frame_ids(session: CompanionSession, page_id: str) -> list[str]:
    return [f.id for f in session.frames if f.page_id == page_id]


async def finalize_current_page(session: CompanionSession) -> HomeworkEntry | None:
    if not session.merged_parts:
        return None
    page_id = session.current_page_id
    timings = estimate_question_timings(session, page_id=page_id)
    entry_meta = dict(session.meta or {})
    entry_meta["question_timings"] = [t.model_dump(mode="json") for t in timings]
    entry_meta["question_frame_map"] = build_question_frame_map(session, page_id)
    entry = HomeworkEntry(
        id=str(uuid.uuid4()),
        session_id=session.id,
        page_id=page_id,
        finalized_at=ISO(),
        source_frame_ids=page_frame_ids(session, page_id),
        parts=[p.model_copy(deep=True) for p in session.merged_parts],
        meta=entry_meta,
    )
    store.append_event(
        session,
        "PAGE_FINALIZED",
        {"homework_id": entry.id, "page_id": entry.page_id},
    )
    sid = student_id(session)
    result = await ingest_homework(sid, entry)
    if result is None:
        logger.warning("Buddy ingest failed; homework not persisted in A layer")
    return entry


async def emit_merge_growth(session: CompanionSession, merge_diff) -> None:
    if not merge_diff or not merge_diff.handwriting_growth:
        return
    sid = student_id(session)
    for qid, growth in merge_diff.handwriting_growth.items():
        await emit_handwriting_growth(sid, session.id, str(qid), growth)
