"""Estimate per-question dwell time from progressive frame captures (⑥ companion)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.companion import merge
from app.schemas import CompanionSession, FrameRecord, PagePart, QuestionTimingEstimate

DurationSource = str  # next_answer | page_end


def _parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _has_grounded_answer(p: PagePart) -> bool:
    if p.kind != "question" or not p.id:
        return False
    if not (p.handwriting or "").strip():
        return False
    bb = p.bbox_handwriting or []
    return len(bb) >= 4


def _replay_first_answer_times(
    frames: list[FrameRecord],
    *,
    page_id: str,
) -> dict[str, datetime]:
    """Map question_id -> first frame time when a grounded handwriting answer appears."""
    page_frames = [
        f
        for f in frames
        if f.page_id == page_id and f.scheduler_action != "skip"
    ]
    page_frames.sort(key=lambda f: f.seq)

    merged: list[PagePart] = []
    first_at: dict[str, datetime] = {}

    for frame in page_frames:
        if frame.scheduler_action == "init":
            merged = [p.model_copy(deep=True) for p in frame.ocr_parts]
        elif frame.scheduler_action == "new_page":
            merged = [p.model_copy(deep=True) for p in frame.ocr_parts]
        else:
            merged = merge.merge_parts(merged, frame.ocr_parts)

        captured = _parse_iso(frame.captured_at)
        for p in merged:
            if _has_grounded_answer(p) and p.id and p.id not in first_at:
                first_at[p.id] = captured

    return first_at


def _page_end_time(
    session: CompanionSession,
    *,
    page_id: str,
    page_frames: list[FrameRecord],
) -> tuple[datetime, DurationSource]:
    if session.status == "ended" and session.ended_at:
        return _parse_iso(session.ended_at), "page_end"
    if page_frames:
        return _parse_iso(page_frames[-1].captured_at), "page_end"
    return datetime.now(timezone.utc), "page_end"


def estimate_question_timings(
    session: CompanionSession,
    *,
    page_id: str | None = None,
) -> list[QuestionTimingEstimate]:
    """
    Dwell time for question Qᵢ ≈ (first answer appears on Qᵢ₊₁) − (first answer on Qᵢ).

    Questions are ordered by **when their answer first appeared**, not by题号.
    The last answered question uses page_end (last frame or session end) as the upper bound.
    """
    pid = page_id or session.current_page_id
    page_frames = [
        f
        for f in session.frames
        if f.page_id == pid and f.scheduler_action != "skip"
    ]
    page_frames.sort(key=lambda f: f.seq)

    first_at = _replay_first_answer_times(session.frames, page_id=pid)
    if not first_at:
        return []

    chronological = sorted(first_at.items(), key=lambda item: item[1])
    end_ts, end_source = _page_end_time(session, page_id=pid, page_frames=page_frames)

    out: list[QuestionTimingEstimate] = []
    for order_idx, (qid, t_start) in enumerate(chronological, start=1):
        if order_idx < len(chronological):
            _next_qid, t_end = chronological[order_idx]
            source: DurationSource = "next_answer"
        else:
            t_end = end_ts
            source = end_source

        duration_ms = max(0, int((t_end - t_start).total_seconds() * 1000))
        out.append(
            QuestionTimingEstimate(
                page_id=pid,
                question_id=qid,
                first_answer_at=t_start.isoformat(),
                duration_ms=duration_ms,
                duration_source=source,
                answer_order=order_idx,
            )
        )

    return out


def refresh_session_question_timings(session: CompanionSession) -> list[QuestionTimingEstimate]:
    timings = estimate_question_timings(session, page_id=session.current_page_id)
    session.question_timings = timings
    return timings
