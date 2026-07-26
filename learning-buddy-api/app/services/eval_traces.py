"""Homework-level eval_trace aggregation for debugging."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.eval_file_logger import today_log_basename
from app.db.models import HomeworkEntry, QuestionEntity
from app.schemas.common import HomeworkEvalTracesOut, QuestionEvalTraceOut
from app.services.eval_util import parse_eval_blob


def build_homework_eval_traces(
    session: Session,
    student_id: str,
    homework_id: str,
) -> HomeworkEvalTracesOut | None:
    entry = session.get(HomeworkEntry, homework_id)
    if entry is None or entry.student_id != student_id:
        return None
    rows = (
        session.query(QuestionEntity)
        .filter(
            QuestionEntity.homework_id == homework_id,
            QuestionEntity.student_id == student_id,
        )
        .order_by(QuestionEntity.ordinal)
        .all()
    )
    questions: list[QuestionEvalTraceOut] = []
    trace_ms: list[int] = []
    trace_count = 0
    for q in rows:
        ev = parse_eval_blob(q.eval_json)
        tr = ev.get("eval_trace") if isinstance(ev.get("eval_trace"), dict) else {}
        if tr.get("trace_version"):
            trace_count += 1
            if tr.get("total_ms") is not None:
                trace_ms.append(int(tr["total_ms"]))
        questions.append(
            QuestionEvalTraceOut(
                question_id=q.id,
                ordinal=str(q.ordinal),
                is_wrong=q.is_wrong,
                reference_answer=ev.get("reference_answer"),
                eval_confidence=q.eval_confidence,
                eval_trace=tr,
            )
        )
    avg_ms = int(sum(trace_ms) / len(trace_ms)) if trace_ms else 0
    return HomeworkEvalTracesOut(
        homework_id=homework_id,
        student_id=student_id,
        trace_count=trace_count,
        question_count=len(rows),
        avg_ms=avg_ms,
        log_file=today_log_basename(),
        questions=questions,
    )
