from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.clients.vllm import chat_completion, parse_json_block
from app.db.models import MasteryView, MemoryEvent
from app.util.json_util import dumps

logger = logging.getLogger(__name__)

DIAG_SYSTEM = "你是诊断智能体。只输出 JSON: {weak_kps:[], summary:string}，禁止面向学生的话术。"
PLAN_SYSTEM = "你是规划智能体。只输出 JSON: {focus_kps:[], actions:[], horizon_days:int}，禁止面向学生的话术。"


def run_diagnosis(session: Session, student_id: str, subject: str, grade_band: str) -> str:
    rows = (
        session.query(MasteryView)
        .filter(
            MasteryView.student_id == student_id,
            MasteryView.subject == subject,
            MasteryView.grade_band == grade_band,
        )
        .all()
    )
    payload = [
        {"kp_id": r.kp_id, "mastery": r.mastery, "weak": r.weak, "confidence": r.confidence}
        for r in rows
    ]
    detail = "empty"
    try:
        raw = chat_completion(DIAG_SYSTEM, f"subject={subject} grade={grade_band}\n{payload}")
        data = parse_json_block(raw)
        detail = data.get("summary", "ok")[:80]
        session.add(
            MemoryEvent(
                student_id=student_id,
                session_id="",
                ts="",
                type="DIAGNOSIS_NOTE",
                payload_json=dumps(data),
            )
        )
        session.flush()
    except Exception as e:
        logger.warning("diagnosis failed: %s", e)
    return detail


def run_planning(session: Session, student_id: str, subject: str, grade_band: str) -> str:
    detail = "empty"
    try:
        raw = chat_completion(PLAN_SYSTEM, f"student={student_id} subject={subject} grade={grade_band}")
        data = parse_json_block(raw)
        detail = f"focus={len(data.get('focus_kps', []))}"
        session.add(
            MemoryEvent(
                student_id=student_id,
                session_id="",
                ts="",
                type="PLAN_NOTE",
                payload_json=dumps(data),
            )
        )
        session.flush()
    except Exception as e:
        logger.warning("planning failed: %s", e)
    return detail
