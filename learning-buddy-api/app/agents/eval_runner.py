from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.agents.eval_agent import evaluate_question_full
from app.config import settings
from app.db.models import MemoryEvent, QuestionEntity, Student
from app.rag.retriever import retrieve_eval_context
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)


def run_eval_on_homework(
    session: Session,
    student_id: str,
    homework_id: str,
    subject: str,
    grade_band: str,
) -> int:
    from app.services.monetization import has_feature

    st = session.get(Student, student_id)
    if st and not has_feature(st, "eval"):
        logger.info("eval skipped: tier lacks eval feature student=%s", student_id)
        return 0
    rows = (
        session.query(QuestionEntity)
        .filter(
            QuestionEntity.homework_id == homework_id,
            QuestionEntity.student_id == student_id,
        )
        .all()
    )
    updated = 0
    for q in rows:
        if not (q.print_text or "").strip():
            continue
        ctx = retrieve_eval_context(q.print_text, subject=subject, grade_band=grade_band)
        ev = evaluate_question_full(
            print_text=q.print_text,
            handwriting=q.handwriting or "",
            eval_context=ctx.to_dict(),
            subject=subject,
            grade_band=grade_band,
            reference_answer=_lookup_bank_answer(q.print_text, ctx),
        )
        if not ev:
            continue
        if ev.get("kp_id"):
            q.kp_tags_json = dumps(
                [
                    {
                        "kp_id": ev["kp_id"],
                        "score": float(ev.get("kp_confidence", 0.7)),
                        "source": "eval_agent",
                        "label": ev.get("kp_id"),
                    }
                ]
            )
        if ev.get("is_wrong") is True:
            q.is_wrong = True
        elif ev.get("is_wrong") is False:
            q.is_wrong = False
        conf = ev.get("eval_confidence")
        if conf is not None:
            q.eval_confidence = float(conf)
        q.eval_json = dumps(ev)
        session.add(
            MemoryEvent(
                student_id=student_id,
                session_id="",
                ts=q.created_at,
                type="EVAL_AGENT_NOTE",
                question_id=q.id,
                kp_id=ev.get("kp_id"),
                payload_json=dumps({"eval": ev}),
            )
        )
        if ev.get("eval_confidence") is not None and float(ev["eval_confidence"]) >= settings.eval_confidence_threshold:
            session.add(
                MemoryEvent(
                    student_id=student_id,
                    session_id="",
                    ts=q.created_at,
                    type="QUESTION_EVALUATED",
                    question_id=q.id,
                    kp_id=ev.get("kp_id"),
                    payload_json=dumps({"eval": ev}),
                )
            )
        updated += 1
    session.flush()
    return updated


def _lookup_bank_answer(print_text: str, ctx) -> str | None:
    for h in ctx.hits:
        pass
    for m in ctx.methods:
        ans = m.get("meta", {}).get("answer")
        if ans:
            return str(ans)
    return None
