"""Staggered OCR/refine batch pipeline for companion sessions."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException

from app.companion import frame_ops, image_diff, session_helpers, store
from app.schemas import (
    AddFramesBatchResponse,
    CompanionSession,
    FrameBatchItem,
    FrameRecord,
    HomeworkEntry,
)

logger = logging.getLogger(__name__)

ISO = lambda: datetime.now(timezone.utc).isoformat()


@dataclass
class _FrameInput:
    image_bytes: bytes
    mime: str
    img_size: tuple[int, int] | None


@dataclass
class _PendingFrame:
    frame_id: str
    seq: int
    captured_at: str
    ocr_parts: list
    ocr_skipped: bool
    diff_ratio: float | None
    ocr_ms: int
    refine_ms: int
    total_ms: int


async def _refine_task(
    parts: list,
    *,
    student_id: str,
    subject: str,
    grade_band: str,
    frame_id: str,
    session: CompanionSession,
) -> tuple[list, int, int]:
    from app.config import settings

    if not settings.refine_enabled or not parts:
        return parts, 0, 0
    refined, refine_ms, n_changes = await frame_ops.refine_frame(
        parts,
        student_id=student_id,
        subject=subject,
        grade_band=grade_band,
    )
    store.append_event(
        session,
        "OCR_REFINED",
        {
            "frame_id": frame_id,
            "changes": n_changes,
            "parts": len(refined),
            "refine_ms": refine_ms,
        },
    )
    return refined, refine_ms, n_changes


async def _commit_pending_frame(
    session: CompanionSession,
    pending: _PendingFrame,
) -> tuple[FrameBatchItem, HomeworkEntry | None]:
    async with frame_ops.session_lock(session.id):
        merge_result = await frame_ops.schedule_merge_for_frame(
            session,
            frame_id=pending.frame_id,
            ocr_parts=pending.ocr_parts,
            ocr_skipped=pending.ocr_skipped,
            finalize_page_fn=session_helpers.finalize_current_page,
            emit_growth_fn=session_helpers.emit_merge_growth,
        )
        frame = FrameRecord(
            id=pending.frame_id,
            seq=pending.seq,
            page_id=session.current_page_id,
            captured_at=pending.captured_at,
            scheduler_action=merge_result.scheduler_action,
            ocr_parts=pending.ocr_parts,
            merge_diff=merge_result.merge_diff,
            same_page_score=merge_result.same_page_score,
            duration_ms=pending.ocr_ms,
            ocr_ms=pending.ocr_ms,
            refine_ms=pending.refine_ms,
            total_ms=pending.total_ms,
            skip_reason=merge_result.skip_reason,
        )
        session.frames.append(frame)
        store.save_session(session)

    item = FrameBatchItem(
        frame_id=pending.frame_id,
        scheduler_action=merge_result.scheduler_action,
        merge_diff=merge_result.merge_diff,
        same_page_score=merge_result.same_page_score,
        skip_reason=merge_result.skip_reason,
        ocr_ms=pending.ocr_ms,
        refine_ms=pending.refine_ms,
        total_ms=pending.total_ms,
        duration_ms=pending.ocr_ms,
    )
    return item, merge_result.homework_finalized


async def _flush_refine_prev(
    session: CompanionSession,
    refine_prev: asyncio.Task,
    pending_prev: _PendingFrame,
    results: list[FrameBatchItem],
    homework_finalized: list[HomeworkEntry],
) -> None:
    refined_parts, refine_ms, _ = await refine_prev
    pending_prev.ocr_parts = refined_parts
    pending_prev.refine_ms = refine_ms
    pending_prev.total_ms = pending_prev.ocr_ms + refine_ms
    store.append_event(
        session,
        "OCR_DONE",
        {
            "frame_id": pending_prev.frame_id,
            "duration_ms": pending_prev.ocr_ms,
            "ocr_ms": pending_prev.ocr_ms,
            "refine_ms": pending_prev.refine_ms,
            "total_ms": pending_prev.total_ms,
        },
    )
    item, hw = await _commit_pending_frame(session, pending_prev)
    results.append(item)
    if hw:
        homework_finalized.append(hw)


async def run_batch_pipeline(
    session_id: str,
    frames: list[_FrameInput],
) -> AddFramesBatchResponse:
    if not frames:
        raise HTTPException(status_code=400, detail="No frames in batch")

    session = store.load_session(session_id)
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session already ended")

    sid = session_helpers.student_id(session)
    subject, grade_band = session_helpers.subject_grade(session)
    batch_t0 = time.perf_counter()
    results: list[FrameBatchItem] = []
    homework_finalized: list[HomeworkEntry] = []

    refine_prev: asyncio.Task | None = None
    pending_prev: _PendingFrame | None = None

    for fin in frames:
        session = store.load_session(session_id)
        seq = len(session.frames) + 1
        frame_id = str(uuid.uuid4())
        captured_at = ISO()
        store.save_frame_preview(sid, session_id, frame_id, fin.image_bytes)
        store.append_event(session, "FRAME_ADDED", {"frame_id": frame_id, "seq": seq})

        session = store.load_session(session_id)
        last_frame = session.frames[-1] if session.frames else None
        prev_bytes = None
        last_ocr_parts = None
        if last_frame:
            prev_bytes = store.read_frame_preview_bytes(sid, session_id, last_frame.id)
            last_ocr_parts = last_frame.ocr_parts

        ocr_skipped = False
        diff_ratio: float | None = None
        if prev_bytes and last_ocr_parts:
            skip, diff_ratio = image_diff.should_skip_ocr(prev_bytes, fin.image_bytes)
            ocr_skipped = skip

        if ocr_skipped and last_ocr_parts:
            if refine_prev is not None and pending_prev is not None:
                await _flush_refine_prev(session, refine_prev, pending_prev, results, homework_finalized)
                refine_prev = None
                pending_prev = None
                session = store.load_session(session_id)

            store.append_event(
                session,
                "OCR_SKIPPED",
                {"frame_id": frame_id, "reason": "no_image_change", "diff_ratio": diff_ratio},
            )
            pending = _PendingFrame(
                frame_id=frame_id,
                seq=seq,
                captured_at=captured_at,
                ocr_parts=[p.model_copy(deep=True) for p in last_ocr_parts],
                ocr_skipped=True,
                diff_ratio=diff_ratio,
                ocr_ms=0,
                refine_ms=0,
                total_ms=0,
            )
            item, hw = await _commit_pending_frame(session, pending)
            results.append(item)
            if hw:
                homework_finalized.append(hw)
            session = store.load_session(session_id)
            continue

        store.append_event(session, "OCR_START", {"frame_id": frame_id})

        if refine_prev is not None and pending_prev is not None:
            ocr_task = asyncio.create_task(
                frame_ops.ocr_frame(fin.image_bytes, fin.mime, img_size=fin.img_size)
            )
            await _flush_refine_prev(session, refine_prev, pending_prev, results, homework_finalized)
            session = store.load_session(session_id)

            ocr_parts, ocr_ms = await ocr_task
            refine_prev = asyncio.create_task(
                _refine_task(
                    ocr_parts,
                    student_id=sid,
                    subject=subject,
                    grade_band=grade_band,
                    frame_id=frame_id,
                    session=session,
                )
            )
            pending_prev = _PendingFrame(
                frame_id=frame_id,
                seq=seq,
                captured_at=captured_at,
                ocr_parts=ocr_parts,
                ocr_skipped=False,
                diff_ratio=diff_ratio,
                ocr_ms=ocr_ms,
                refine_ms=0,
                total_ms=ocr_ms,
            )
        else:
            ocr_parts, ocr_ms = await frame_ops.ocr_frame(
                fin.image_bytes, fin.mime, img_size=fin.img_size
            )
            refine_prev = asyncio.create_task(
                _refine_task(
                    ocr_parts,
                    student_id=sid,
                    subject=subject,
                    grade_band=grade_band,
                    frame_id=frame_id,
                    session=session,
                )
            )
            pending_prev = _PendingFrame(
                frame_id=frame_id,
                seq=seq,
                captured_at=captured_at,
                ocr_parts=ocr_parts,
                ocr_skipped=False,
                diff_ratio=diff_ratio,
                ocr_ms=ocr_ms,
                refine_ms=0,
                total_ms=ocr_ms,
            )

    if refine_prev is not None and pending_prev is not None:
        session = store.load_session(session_id)
        await _flush_refine_prev(session, refine_prev, pending_prev, results, homework_finalized)

    batch_total_ms = int((time.perf_counter() - batch_t0) * 1000)
    session = store.load_session(session_id)
    return AddFramesBatchResponse(
        frames=results,
        batch_total_ms=batch_total_ms,
        homework_finalized=homework_finalized,
        session=session,
    )


async def add_frames_batch(
    session_id: str,
    frame_inputs: list[tuple[bytes, str, tuple[int, int] | None]],
) -> AddFramesBatchResponse:
    inputs = [_FrameInput(image_bytes=b, mime=m, img_size=s) for b, m, s in frame_inputs]
    return await run_batch_pipeline(session_id, inputs)
