from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import MasteryView, QuestionEntity
from app.rag.kp_resolver import canonical_kp_id, resolve_kp_label
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

DEFAULT_EXPECTED_DWELL_MS = 180_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _questions_for_kp(
    session: Session,
    student_id: str,
    kp_id: str,
    subject: str,
    grade_band: str,
) -> list[QuestionEntity]:
    rows = (
        session.query(QuestionEntity)
        .filter(
            QuestionEntity.student_id == student_id,
            QuestionEntity.subject == subject,
            QuestionEntity.grade_band == grade_band,
        )
        .all()
    )
    matched: list[QuestionEntity] = []
    cid = canonical_kp_id(kp_id)
    for q in rows:
        tags = loads(q.kp_tags_json, [])
        if any(canonical_kp_id(t.get("kp_id") or "") == cid for t in tags):
            matched.append(q)
    return matched


def _expected_dwell_ms(q: QuestionEntity) -> int:
    ev = loads(q.rag_evidence_json, [])
    for e in ev:
        if isinstance(e, dict) and e.get("expected_dwell_sec"):
            return int(float(e["expected_dwell_sec"]) * 1000)
    return DEFAULT_EXPECTED_DWELL_MS


def _fluency_and_wrong(questions: list[QuestionEntity]) -> tuple[float, float, int]:
    ratios: list[float] = []
    wrong = 0
    evaluated = 0
    for q in questions:
        hw = (q.handwriting or "").strip()
        if not hw:
            continue
        if q.is_wrong is not None:
            evaluated += 1
            if q.is_wrong:
                wrong += 1
        if q.dwell_ms and q.dwell_ms > 0:
            expected = _expected_dwell_ms(q)
            if expected > 0:
                ratios.append(q.dwell_ms / expected)
    wrong_rate = (wrong / evaluated) if evaluated else 0.0
    fluency_ratio = statistics.median(ratios) if ratios else 0.0
    return fluency_ratio, wrong_rate, evaluated


def _aggregate_kp_rules(
    session: Session,
    student_id: str,
    kp_id: str,
    subject: str,
    grade_band: str,
) -> MasteryView:
    questions = _questions_for_kp(session, student_id, kp_id, subject, grade_band)
    now = _now_iso()
    fluency_ratio, wrong_rate, evaluated = _fluency_and_wrong(questions)

    if not questions:
        mastery, confidence, weak = "unknown", 0.0, False
    else:
        has_hw = any((q.handwriting or "").strip() for q in questions)
        any_wrong = any(q.is_wrong is True for q in questions)
        if any_wrong and fluency_ratio > 1.5:
            mastery, confidence, weak = "learning", 0.65, True
        elif any_wrong:
            mastery, confidence, weak = "learning", 0.6, True
        elif has_hw and fluency_ratio and fluency_ratio <= 1.2:
            mastery, confidence, weak = "learning", 0.75, False
        elif has_hw:
            mastery, confidence, weak = "learning", 0.5, False
        else:
            mastery, confidence, weak = "unknown", 0.2, False

        if has_hw and not any_wrong and fluency_ratio and fluency_ratio <= 1.0 and evaluated >= 2:
            mastery, confidence = "mastered", max(confidence, 0.85)

    existing = session.get(MasteryView, {"student_id": student_id, "kp_id": kp_id})
    if existing and existing.mastery == "mastered":
        mastery = "mastered"
        confidence = max(confidence, 0.8)

    evidence = [
        {
            "homework_id": q.homework_id,
            "question_id": q.id,
            "ts": q.created_at,
            "dwell_ms": q.dwell_ms,
            "note": "wrong" if q.is_wrong else ("handwriting" if (q.handwriting or "").strip() else "seen"),
        }
        for q in questions
    ]

    row = MasteryView(
        student_id=student_id,
        kp_id=kp_id,
        subject=subject,
        grade_band=grade_band,
        label=resolve_kp_label(canonical_kp_id(kp_id)),
        mastery=mastery,
        confidence=confidence,
        weak=weak,
        fluency=max(0.0, 1.0 - fluency_ratio) if fluency_ratio else 0.0,
        fluency_ratio=round(fluency_ratio, 4) if fluency_ratio else 0.0,
        wrong_rate=round(wrong_rate, 4),
        evidence_json=dumps(evidence),
        updated_at=now,
        demo=False,
    )
    session.merge(row)
    return row


def recompute_for_kps(
    session: Session,
    student_id: str,
    kp_ids: set[str],
    *,
    subject: str = "math",
    grade_band: str = "hs",
) -> int:
    count = 0
    for kp_id in kp_ids:
        if kp_id:
            _aggregate_kp_rules(session, student_id, kp_id, subject, grade_band)
            count += 1
    session.flush()
    return count


def recompute_student(session: Session, student_id: str) -> int:
    rows = session.query(QuestionEntity).filter(QuestionEntity.student_id == student_id).all()
    by_scope: dict[tuple[str, str], set[str]] = {}
    for q in rows:
        scope = (q.subject, q.grade_band)
        for t in loads(q.kp_tags_json, []):
            kid = canonical_kp_id(t.get("kp_id") or "")
            if kid:
                by_scope.setdefault(scope, set()).add(kid)
    total = 0
    for (subj, gb), kp_ids in by_scope.items():
        total += recompute_for_kps(session, student_id, kp_ids, subject=subj, grade_band=gb)
    return total


def run_aplus_aggregate_for_homework(
    session: Session,
    student_id: str,
    homework_id: str,
    subject: str,
    grade_band: str,
) -> int:
    rows = (
        session.query(QuestionEntity)
        .filter(
            QuestionEntity.homework_id == homework_id,
            QuestionEntity.student_id == student_id,
        )
        .all()
    )
    kp_ids: set[str] = set()
    for q in rows:
        for t in loads(q.kp_tags_json, []):
            kid = t.get("kp_id")
            if kid:
                kp_ids.add(kid)
        if q.eval_confidence is not None and q.eval_confidence < settings.eval_confidence_threshold:
            continue
    if not kp_ids:
        return 0
    return recompute_for_kps(session, student_id, kp_ids, subject=subject, grade_band=grade_band)


def patch_mastery(
    session: Session,
    student_id: str,
    kp_id: str,
    mastery: str,
    *,
    subject: str = "math",
    grade_band: str = "hs",
) -> MasteryView | None:
    row = session.get(MasteryView, {"student_id": student_id, "kp_id": kp_id})
    if row is None:
        row = MasteryView(
            student_id=student_id,
            kp_id=kp_id,
            subject=subject,
            grade_band=grade_band,
            label=resolve_kp_label(canonical_kp_id(kp_id)),
            demo=False,
        )
    row.mastery = mastery
    row.updated_at = _now_iso()
    if mastery == "mastered":
        row.confidence = max(row.confidence, 0.9)
        row.weak = False
    session.merge(row)
    session.flush()
    return row
