"""Companion session service — OCR, schedule, merge, finalize."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from app.companion import image_diff, merge, store
from app.schemas import (
    AddFrameResponse,
    CompanionSession,
    EndSessionResponse,
    FinalizePageResponse,
    FrameRecord,
    HomeworkEntry,
    MergeDiff,
    PagePart,
)
from app.vllm_client import vllm_client

ISO = lambda: datetime.now(timezone.utc).isoformat()


def _page_frame_ids(session: CompanionSession, page_id: str) -> list[str]:
    return [f.id for f in session.frames if f.page_id == page_id]


def _finalize_current_page(session: CompanionSession) -> HomeworkEntry | None:
    if not session.merged_parts:
        return None
    entry = HomeworkEntry(
        id=str(uuid.uuid4()),
        session_id=session.id,
        page_id=session.current_page_id,
        finalized_at=ISO(),
        source_frame_ids=_page_frame_ids(session, session.current_page_id),
        parts=[p.model_copy(deep=True) for p in session.merged_parts],
        meta=dict(session.meta),
    )
    store.save_homework(entry)
    store.update_mastery_from_homework(entry)
    store.append_event(
        session,
        "PAGE_FINALIZED",
        {"homework_id": entry.id, "page_id": entry.page_id},
    )
    return entry


def create_session(subject: str | None = None, note: str | None = None) -> CompanionSession:
    return store.create_session(subject=subject, note=note)


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

    seq = len(session.frames) + 1
    frame_id = str(uuid.uuid4())
    captured_at = ISO()
    store.save_frame_preview(session_id, frame_id, image_bytes)
    store.append_event(session, "FRAME_ADDED", {"frame_id": frame_id, "seq": seq})

    ocr_skipped = False
    diff_ratio: float | None = None
    last_frame = session.frames[-1] if session.frames else None
    if last_frame:
        prev_bytes = store.read_frame_preview_bytes(session_id, last_frame.id)
        if prev_bytes:
            skip, diff_ratio = image_diff.should_skip_ocr(prev_bytes, image_bytes)
            if skip:
                ocr_skipped = True

    if ocr_skipped and last_frame:
        store.append_event(
            session,
            "OCR_SKIPPED",
            {"frame_id": frame_id, "reason": "no_image_change", "diff_ratio": diff_ratio},
        )
        ocr_parts = [p.model_copy(deep=True) for p in last_frame.ocr_parts]
        duration_ms = 0
    else:
        store.append_event(session, "OCR_START", {"frame_id": frame_id})
        t0 = time.perf_counter()
        ocr_result = await vllm_client.analyze_page(image_bytes, mime, img_size=img_size)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        ocr_parts = ocr_result.parts
        store.append_event(session, "OCR_DONE", {"frame_id": frame_id, "duration_ms": duration_ms})

    homework_finalized: HomeworkEntry | None = None
    scheduler_action = "init"
    merge_diff: MergeDiff | None = None
    same_page_score: float | None = None
    skip_reason: str | None = None

    if not session.frames:
        session.merged_parts = [p.model_copy(deep=True) for p in ocr_parts]
        scheduler_action = "init"
        merge_diff = merge.diff_parts([], session.merged_parts)
        store.append_event(session, "MERGED", {"frame_id": frame_id, "action": "init"})
    else:
        same, same_page_score = merge.is_same_page(session.merged_parts, ocr_parts)
        if not same and session.merged_parts:
            homework_finalized = _finalize_current_page(session)
            session.current_page_id = store.new_page_id()
            session.merged_parts = [p.model_copy(deep=True) for p in ocr_parts]
            scheduler_action = "new_page"
            merge_diff = merge.diff_parts([], session.merged_parts)
            store.append_event(
                session,
                "NEW_PAGE",
                {
                    "frame_id": frame_id,
                    "homework_id": homework_finalized.id if homework_finalized else None,
                    "new_page_id": session.current_page_id,
                },
            )
            store.append_event(session, "MERGED", {"frame_id": frame_id, "action": "new_page"})
        elif not merge.has_handwriting_growth(session.merged_parts, ocr_parts):
            scheduler_action = "skip"
            skip_reason = "no_image_change" if ocr_skipped else "no_handwriting_growth"
            merge_diff = MergeDiff()
            store.append_event(session, "SKIPPED", {"frame_id": frame_id, "reason": skip_reason})
        else:
            before = [p.model_copy(deep=True) for p in session.merged_parts]
            session.merged_parts = merge.merge_parts(session.merged_parts, ocr_parts)
            scheduler_action = "merge"
            merge_diff = merge.diff_parts(before, session.merged_parts)
            store.append_event(session, "MERGED", {"frame_id": frame_id, "action": "merge"})

    frame = FrameRecord(
        id=frame_id,
        seq=seq,
        page_id=session.current_page_id,
        captured_at=captured_at,
        scheduler_action=scheduler_action,
        ocr_parts=ocr_parts,
        merge_diff=merge_diff,
        same_page_score=same_page_score,
        duration_ms=duration_ms,
        skip_reason=skip_reason,
    )
    session.frames.append(frame)
    store.save_session(session)

    return AddFrameResponse(
        frame_id=frame_id,
        scheduler_action=scheduler_action,
        merge_diff=merge_diff,
        same_page_score=same_page_score,
        skip_reason=skip_reason,
        ocr_parts=ocr_parts,
        merged_parts=session.merged_parts,
        duration_ms=duration_ms,
        homework_finalized=homework_finalized,
        session=session,
    )


def finalize_page(session_id: str) -> FinalizePageResponse:
    session = store.load_session(session_id)
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session already ended")
    homework = _finalize_current_page(session)
    session.merged_parts = []
    session.current_page_id = store.new_page_id()
    store.save_session(session)
    return FinalizePageResponse(homework=homework, session=session)


def end_session(session_id: str) -> EndSessionResponse:
    session = store.load_session(session_id)
    if session.status == "ended":
        return EndSessionResponse(homework=None, session=session)
    homework = None
    if session.merged_parts:
        homework = _finalize_current_page(session)
    session.status = "ended"
    session.ended_at = ISO()
    session.merged_parts = []
    store.append_event(session, "SESSION_END", {})
    store.save_session(session)
    return EndSessionResponse(homework=homework, session=session)


def export_session(session_id: str) -> dict:
    session = store.load_session(session_id)
    homework = [h for h in store.list_homework() if h.session_id == session_id]
    mastery_ids = set()
    for h in homework:
        for p in h.parts:
            if p.kind == "question" and p.id:
                mastery_ids.add(f"q_{p.id}")
    mastery = [m for m in store.list_mastery() if m.kp_id in mastery_ids]
    return {
        "session": session.model_dump(mode="json", by_alias=True),
        "homework": [h.model_dump(mode="json", by_alias=True) for h in homework],
        "mastery": [m.model_dump(mode="json", by_alias=True) for m in mastery],
    }
