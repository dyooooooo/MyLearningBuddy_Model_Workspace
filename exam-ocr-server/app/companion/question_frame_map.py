"""Map each question ordinal to the frame with the longest handwriting (for bbox preview)."""

from __future__ import annotations

from app.schemas import CompanionSession, FrameRecord


def build_question_frame_map(session: CompanionSession, page_id: str) -> dict[str, str]:
    best_frame: dict[str, str] = {}
    best_len: dict[str, int] = {}
    for frame in session.frames:
        if frame.page_id != page_id:
            continue
        for part in frame.ocr_parts or []:
            if part.kind != "question" or not part.id:
                continue
            qid = str(part.id)
            hw_len = len((part.handwriting or "").strip())
            if hw_len > best_len.get(qid, -1):
                best_len[qid] = hw_len
                best_frame[qid] = frame.id
    if not best_frame and session.frames:
        last = next((f for f in reversed(session.frames) if f.page_id == page_id), None)
        if last:
            for part in last.ocr_parts or []:
                if part.kind == "question" and part.id:
                    best_frame.setdefault(str(part.id), last.id)
    return best_frame
