"""Shared per-frame OCR, refine, and merge scheduling."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.companion import image_diff, merge, store
from app.companion.question_timing import refresh_session_question_timings
from app.config import settings
from app.math_enrich import enrich_page_parts
from app.ocr_refine import refine_parts
from app.schemas import CompanionSession, HomeworkEntry, MergeDiff, PagePart, SchedulerAction
from app.vllm_client import vllm_client

logger = logging.getLogger(__name__)

_session_locks: dict[str, asyncio.Lock] = {}


def session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


@dataclass
class OcrRefineResult:
    ocr_parts: list[PagePart]
    vision_parts: list[PagePart]
    ocr_skipped: bool
    diff_ratio: float | None
    ocr_ms: int
    refine_ms: int
    total_ms: int
    n_refine_changes: int


@dataclass
class MergeScheduleResult:
    scheduler_action: SchedulerAction
    merge_diff: MergeDiff | None
    same_page_score: float | None
    skip_reason: str | None
    homework_finalized: HomeworkEntry | None


async def ocr_frame(
    image_bytes: bytes,
    mime: str,
    *,
    img_size: tuple[int, int] | None = None,
) -> tuple[list[PagePart], int]:
    t0 = time.perf_counter()
    result = await vllm_client.analyze_page(image_bytes, mime, img_size=img_size)
    ocr_ms = int((time.perf_counter() - t0) * 1000)
    return result.parts, ocr_ms


async def refine_frame(
    parts: list[PagePart],
    *,
    student_id: str,
    subject: str,
    grade_band: str,
) -> tuple[list[PagePart], int, int]:
    if not settings.refine_enabled or not parts:
        return parts, 0, 0
    t0 = time.perf_counter()
    refined, n_changes = await refine_parts(
        parts,
        student_id=student_id,
        subject=subject,
        grade_band=grade_band,
    )
    refine_ms = int((time.perf_counter() - t0) * 1000)
    return refined, refine_ms, n_changes


async def run_ocr_refine_sequential(
    *,
    session: CompanionSession,
    frame_id: str,
    image_bytes: bytes,
    mime: str,
    img_size: tuple[int, int] | None,
    student_id: str,
    subject: str,
    grade_band: str,
    last_frame_ocr_parts: list[PagePart] | None,
    prev_image_bytes: bytes | None,
) -> OcrRefineResult:
    ocr_skipped = False
    diff_ratio: float | None = None
    if prev_image_bytes:
        skip, diff_ratio = image_diff.should_skip_ocr(prev_image_bytes, image_bytes)
        if skip and last_frame_ocr_parts:
            ocr_skipped = True

    if ocr_skipped and last_frame_ocr_parts:
        store.append_event(
            session,
            "OCR_SKIPPED",
            {"frame_id": frame_id, "reason": "no_image_change", "diff_ratio": diff_ratio},
        )
        parts = [p.model_copy(deep=True) for p in last_frame_ocr_parts]
        return OcrRefineResult(
            ocr_parts=parts,
            vision_parts=[p.model_copy(deep=True) for p in parts],
            ocr_skipped=True,
            diff_ratio=diff_ratio,
            ocr_ms=0,
            refine_ms=0,
            total_ms=0,
            n_refine_changes=0,
        )

    store.append_event(session, "OCR_START", {"frame_id": frame_id})
    t_total = time.perf_counter()
    vision_parts, ocr_ms = await ocr_frame(image_bytes, mime, img_size=img_size)
    vision_parts = await enrich_page_parts(vision_parts)
    ocr_parts = vision_parts
    refine_ms = 0
    n_changes = 0
    if settings.refine_enabled and vision_parts:
        ocr_parts, refine_ms, n_changes = await refine_frame(
            vision_parts,
            student_id=student_id,
            subject=subject,
            grade_band=grade_band,
        )
        ocr_parts = await enrich_page_parts(ocr_parts)
        store.append_event(
            session,
            "OCR_REFINED",
            {
                "frame_id": frame_id,
                "changes": n_changes,
                "parts": len(ocr_parts),
                "refine_ms": refine_ms,
            },
        )
    total_ms = int((time.perf_counter() - t_total) * 1000)
    store.append_event(
        session,
        "OCR_DONE",
        {
            "frame_id": frame_id,
            "duration_ms": ocr_ms,
            "ocr_ms": ocr_ms,
            "refine_ms": refine_ms,
            "total_ms": total_ms,
        },
    )
    return OcrRefineResult(
        ocr_parts=ocr_parts,
        vision_parts=[p.model_copy(deep=True) for p in vision_parts],
        ocr_skipped=False,
        diff_ratio=diff_ratio,
        ocr_ms=ocr_ms,
        refine_ms=refine_ms,
        total_ms=total_ms,
        n_refine_changes=n_changes,
    )


async def schedule_merge_for_frame(
    session: CompanionSession,
    *,
    frame_id: str,
    ocr_parts: list[PagePart],
    ocr_skipped: bool,
    finalize_page_fn,
    emit_growth_fn,
) -> MergeScheduleResult:
    homework_finalized: HomeworkEntry | None = None
    scheduler_action: SchedulerAction = "init"
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
            homework_finalized = await finalize_page_fn(session)
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
            await emit_growth_fn(session, merge_diff)

    refresh_session_question_timings(session)

    return MergeScheduleResult(
        scheduler_action=scheduler_action,
        merge_diff=merge_diff,
        same_page_score=same_page_score,
        skip_reason=skip_reason,
        homework_finalized=homework_finalized,
    )
