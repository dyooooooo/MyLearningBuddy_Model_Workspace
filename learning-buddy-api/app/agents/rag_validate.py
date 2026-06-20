from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.rag.retriever import map_question_to_kp, retrieve_eval_context
from app.db.models import QuestionEntity
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)


def run_rag_validate_on_homework(
    session: Session,
    student_id: str,
    homework_id: str,
    subject: str,
    grade_band: str,
) -> int:
    """KP validation / tag refresh only — no correctness eval."""
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
        tags, evidence = map_question_to_kp(q.print_text, subject=subject, grade_band=grade_band)
        if ctx.kps and not tags:
            top = ctx.kps[0]
            tags = [
                {
                    "kp_id": top["meta"].get("kp_id") or top["id"],
                    "score": 0.6,
                    "source": "graph_expand",
                    "label": top.get("label", ""),
                }
            ]
        if tags:
            q.kp_tags_json = dumps(tags)
            q.rag_evidence_json = dumps(evidence + [{"graph": ctx.to_dict()}])
            updated += 1
    session.flush()
    return updated
