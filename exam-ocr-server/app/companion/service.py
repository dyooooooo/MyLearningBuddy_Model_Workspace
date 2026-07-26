"""Companion session service — OCR, schedule, merge, finalize."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.companion import frame_ops, session_helpers, store
from app.companion.pipeline import add_frames_batch
from app.schemas import (
    AddFrameResponse,
    AddFramesBatchResponse,
    CompanionSession,
    EndSessionResponse,
    FinalizePageResponse,
    FrameRecord,
    HomeworkEntry,
)

logger = logging.getLogger(__name__)

ISO = session_helpers.ISO


def create_session(
    student_id: str,
    subject: str | None = None,
    note: str | None = None,
) -> CompanionSession:
    return store.create_session(student_id=student_id, subject=subject, note=note)


def get_session(session_id: str) -> CompanionSession:
    return store.load_session(session_id)


async def add_frame(
    session_id: str,
    image_bytes: bytes,
    mime: str,
    *,
    img_size: tuple[int, int] | None = None,
) -> AddFrameResponse:
    session = store.load_session(session_id)
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session already ended")

    sid = session_helpers.student_id(session)
    subject, grade_band = session_helpers.subject_grade(session)
    seq = len(session.frames) + 1
    frame_id = str(uuid.uuid4())
    captured_at = ISO()
    store.save_frame_preview(sid, session_id, frame_id, image_bytes)
    store.append_event(session, "FRAME_ADDED", {"frame_id": frame_id, "seq": seq})

    last_frame = session.frames[-1] if session.frames else None
    prev_bytes = None
    last_ocr_parts = None
    if last_frame:
        prev_bytes = store.read_frame_preview_bytes(sid, session_id, last_frame.id)
        last_ocr_parts = last_frame.ocr_parts

    ocr_result = await frame_ops.run_ocr_refine_sequential(
        session=session,
        frame_id=frame_id,
        image_bytes=image_bytes,
        mime=mime,
        img_size=img_size,
        student_id=sid,
        subject=subject,
        grade_band=grade_band,
        last_frame_ocr_parts=last_ocr_parts,
        prev_image_bytes=prev_bytes,
    )

    async with frame_ops.session_lock(session_id):
        merge_result = await frame_ops.schedule_merge_for_frame(
            session,
            frame_id=frame_id,
            ocr_parts=ocr_result.ocr_parts,
            ocr_skipped=ocr_result.ocr_skipped,
            finalize_page_fn=session_helpers.finalize_current_page,
            emit_growth_fn=session_helpers.emit_merge_growth,
        )
        frame = FrameRecord(
            id=frame_id,
            seq=seq,
            page_id=session.current_page_id,
            captured_at=captured_at,
            scheduler_action=merge_result.scheduler_action,
            ocr_parts=ocr_result.ocr_parts,
            vision_parts=ocr_result.vision_parts,
            n_refine_changes=ocr_result.n_refine_changes,
            merge_diff=merge_result.merge_diff,
            same_page_score=merge_result.same_page_score,
            duration_ms=ocr_result.ocr_ms,
            ocr_ms=ocr_result.ocr_ms,
            refine_ms=ocr_result.refine_ms,
            total_ms=ocr_result.total_ms,
            skip_reason=merge_result.skip_reason,
        )
        session.frames.append(frame)
        store.save_session(session)

    session = store.load_session(session_id)
    return AddFrameResponse(
        frame_id=frame_id,
        scheduler_action=merge_result.scheduler_action,
        merge_diff=merge_result.merge_diff,
        same_page_score=merge_result.same_page_score,
        skip_reason=merge_result.skip_reason,
        ocr_parts=ocr_result.ocr_parts,
        merged_parts=session.merged_parts,
        duration_ms=ocr_result.ocr_ms,
        ocr_ms=ocr_result.ocr_ms,
        refine_ms=ocr_result.refine_ms,
        total_ms=ocr_result.total_ms,
        homework_finalized=merge_result.homework_finalized,
        session=session,
    )


async def add_frames_batch_endpoint(
    session_id: str,
    frame_inputs: list[tuple[bytes, str, tuple[int, int] | None]],
) -> AddFramesBatchResponse:
    return await add_frames_batch(session_id, frame_inputs)


async def finalize_page(session_id: str) -> FinalizePageResponse:
    session = store.load_session(session_id)
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session already ended")
    homework = await session_helpers.finalize_current_page(session)
    session.merged_parts = []
    session.current_page_id = store.new_page_id()
    store.save_session(session)
    return FinalizePageResponse(homework=homework, session=session)


async def end_session(session_id: str) -> EndSessionResponse:
    session = store.load_session(session_id)
    if session.status == "ended":
        return EndSessionResponse(homework=None, session=session)
    homework = None
    if session.merged_parts:
        homework = await session_helpers.finalize_current_page(session)
    session.status = "ended"
    session.ended_at = ISO()
    session.merged_parts = []
    store.append_event(session, "SESSION_END", {})
    store.save_session(session)
    return EndSessionResponse(homework=homework, session=session)


async def export_session(session_id: str) -> dict:
    from app.buddy_client import proxy_get

    session = store.load_session(session_id)
    sid = session_helpers.student_id(session)
    homework: list = []
    mastery: list = []
    try:
        hr = await proxy_get(f"/api/v1/students/{sid}/homework", sid)
        if hr.status_code == 200:
            homework = [h for h in hr.json() if h.get("session_id") == session_id]
        mr = await proxy_get(f"/api/v1/students/{sid}/mastery", sid)
        if mr.status_code == 200:
            mastery = mr.json()
    except Exception as e:
        logger.warning("Buddy export fetch failed: %s", e)
    return {
        "session": session.model_dump(mode="json", by_alias=True),
        "homework": homework,
        "mastery": mastery,
    }
