"""Tests for per-question frame mapping at finalize."""

from app.companion.question_frame_map import build_question_frame_map
from app.schemas import CompanionSession, FrameRecord, PagePart


def test_build_question_frame_map_picks_longest_handwriting():
    session = CompanionSession(
        id="s1",
        started_at="2026-06-20T12:00:00+00:00",
        current_page_id="p1",
        frames=[
            FrameRecord(
                id="f1",
                seq=1,
                page_id="p1",
                captured_at="2026-06-20T12:00:01+00:00",
                scheduler_action="init",
                ocr_parts=[
                    PagePart(
                        kind="question",
                        id="1",
                        print_text="Q1",
                        handwriting="ab",
                    )
                ],
            ),
            FrameRecord(
                id="f2",
                seq=2,
                page_id="p1",
                captured_at="2026-06-20T12:00:10+00:00",
                scheduler_action="merge",
                ocr_parts=[
                    PagePart(
                        kind="question",
                        id="1",
                        print_text="Q1",
                        handwriting="abcd longer",
                    )
                ],
            ),
        ],
    )
    m = build_question_frame_map(session, "p1")
    assert m["1"] == "f2"


if __name__ == "__main__":
    test_build_question_frame_map_picks_longest_handwriting()
    print("OK")
