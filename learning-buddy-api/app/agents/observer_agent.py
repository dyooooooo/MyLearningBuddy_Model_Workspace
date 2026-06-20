from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clients.vllm import chat_completion, parse_json_block
from app.db.models import MemoryEvent, QuestionEntity
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

OBSERVER_SYSTEM = """你是观察/画像智能体。禁止生成面向学生的讲解或建议。
只输出 JSON：
- patterns: string[] 内部观察模式
- wrong_question_ids: string[]
- kp_ids_to_refresh: string[]"""


def run_observer(
    session: Session,
    student_id: str,
    homework_id: str,
    subject: str,
    grade_band: str,
) -> str:
    rows = (
        session.query(QuestionEntity)
        .filter(
            QuestionEntity.homework_id == homework_id,
            QuestionEntity.student_id == student_id,
        )
        .all()
    )
    summary = [
        {
            "id": q.id,
            "print": (q.print_text or "")[:120],
            "handwriting": (q.handwriting or "")[:80],
            "is_wrong": q.is_wrong,
            "dwell_ms": q.dwell_ms,
            "kp_tags": loads(q.kp_tags_json, []),
        }
        for q in rows
    ]
    detail = "no_pattern"
    try:
        raw = chat_completion(
            OBSERVER_SYSTEM,
            f"homework={homework_id} subject={subject} grade={grade_band}\n{summary}",
            max_tokens=512,
        )
        data = parse_json_block(raw)
        patterns = data.get("patterns") or []
        detail = ";".join(patterns) if patterns else "no_pattern"
        session.add(
            MemoryEvent(
                student_id=student_id,
                session_id="",
                ts=rows[0].created_at if rows else "",
                type="OBSERVER_NOTE",
                payload_json=dumps({"homework_id": homework_id, **data}),
            )
        )
    except Exception as e:
        logger.warning("observer_agent failed: %s", e)
        wrong = [q for q in rows if q.is_wrong is True]
        if wrong:
            detail = f"wrong_count={len(wrong)}"
            session.add(
                MemoryEvent(
                    student_id=student_id,
                    session_id="",
                    ts=rows[0].created_at if rows else "",
                    type="OBSERVER_NOTE",
                    payload_json=dumps(
                        {
                            "homework_id": homework_id,
                            "patterns": [detail],
                            "wrong_question_ids": [q.id for q in wrong[:10]],
                        }
                    ),
                )
            )
    session.flush()
    return detail
