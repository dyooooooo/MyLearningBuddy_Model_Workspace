"""Tests for progressive question dwell-time estimation."""

from __future__ import annotations

from app.companion.question_timing import estimate_question_timings
from app.schemas import CompanionSession, FrameRecord, MergeDiff, PagePart


def _q(qid: str, hw: str = "", bbox: list[float] | None = None) -> PagePart:
    return PagePart(
        kind="question",
        id=qid,
        print_text=f"Q{qid}",
        handwriting=hw,
        bbox_handwriting=bbox or ([0.1, 0.2, 0.3, 0.25] if hw else []),
    )


def _frame(
    seq: int,
    *,
    ts: str,
    parts: list[PagePart],
    action: str = "merge",
    page_id: str = "p1",
) -> FrameRecord:
    return FrameRecord(
        id=f"f{seq}",
        seq=seq,
        page_id=page_id,
        captured_at=ts,
        scheduler_action=action,
        ocr_parts=parts,
        merge_diff=MergeDiff(),
    )


def _session(frames: list[FrameRecord], **kwargs) -> CompanionSession:
    return CompanionSession(
        id="s1",
        started_at="2026-06-19T10:00:00+00:00",
        current_page_id="p1",
        frames=frames,
        **kwargs,
    )


def test_two_questions_next_answer_gap():
    session = _session(
        [
            _frame(1, ts="2026-06-19T10:00:00+00:00", parts=[_q("1", "A")], action="init"),
            _frame(
                2,
                ts="2026-06-19T10:02:30+00:00",
                parts=[_q("1", "A"), _q("2", "B")],
            ),
        ]
    )
    timings = estimate_question_timings(session)
    assert len(timings) == 2
    by_q = {t.question_id: t for t in timings}
    assert by_q["1"].duration_ms == 150_000
    assert by_q["1"].duration_source == "next_answer"
    assert by_q["2"].duration_source == "page_end"
    assert by_q["2"].duration_ms == 0


def test_out_of_order_answer_detection_order():
    session = _session(
        [
            _frame(1, ts="2026-06-19T10:00:00+00:00", parts=[_q("1", "A")], action="init"),
            _frame(
                2,
                ts="2026-06-19T10:01:00+00:00",
                parts=[_q("1", "A"), _q("3", "C")],
            ),
            _frame(
                3,
                ts="2026-06-19T10:03:00+00:00",
                parts=[_q("1", "A"), _q("3", "C"), _q("2", "B")],
            ),
        ]
    )
    timings = estimate_question_timings(session)
    assert [t.question_id for t in timings] == ["1", "3", "2"]
    assert timings[0].duration_ms == 60_000
    assert timings[1].duration_ms == 120_000


def test_no_bbox_handwriting_ignored():
    session = _session(
        [
            _frame(
                1,
                ts="2026-06-19T10:00:00+00:00",
                parts=[PagePart(kind="question", id="1", print_text="Q", handwriting="x")],
                action="init",
            ),
        ]
    )
    assert estimate_question_timings(session) == []
