from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import HomeworkEntry, QuestionEntity
from app.rag.kp_resolver import resolve_kp_label_pair
from app.schemas.common import KpTag, LabelI18n, WrongBookItemOut, WrongBookOut
from app.services.eval_util import candidate_reason, eval_fields_from_question, is_wrong_book_candidate
from app.services.ingest_homework import question_to_out
from app.util.json_util import loads


def _kp_tags_out(raw: list, *, session: Session | None, locale: str) -> list[KpTag]:
    out: list[KpTag] = []
    for t in raw:
        if not isinstance(t, dict):
            continue
        kid = str(t.get("kp_id", ""))
        pair = resolve_kp_label_pair(kid, session=session)
        label = pair.en if locale == "en" else pair.zh
        out.append(
            KpTag(
                kp_id=kid,
                score=float(t.get("score", 0)),
                source=str(t.get("source", "")),
                label=label or str(t.get("label", kid)),
                label_i18n=pair,
            )
        )
    return out


def _to_wrong_item(
    q: QuestionEntity,
    hw: HomeworkEntry,
    *,
    session: Session | None,
    locale: str,
    as_candidate: bool = False,
) -> WrongBookItemOut:
    fields = eval_fields_from_question(q)
    qout = question_to_out(q, session=session, locale=locale)
    print_rich, hw_rich = qout.print_rich, qout.handwriting_rich
    return WrongBookItemOut(
        question_id=q.id,
        homework_id=q.homework_id,
        ordinal=q.ordinal,
        print_text=q.print_text,
        handwriting=q.handwriting,
        print_rich=print_rich,
        handwriting_rich=hw_rich,
        kp_tags=qout.kp_tags,
        finalized_at=hw.finalized_at,
        is_wrong=q.is_wrong,
        wrong_source=fields.get("wrong_source"),
        agent_is_wrong=fields.get("agent_is_wrong"),
        eval_status=fields.get("eval_status"),
        eval_confidence=q.eval_confidence,
        reference_answer=fields.get("reference_answer"),
        match_notes=fields.get("match_notes"),
        eval_json=fields.get("eval_json") or {},
        candidate_reason=candidate_reason(q) if as_candidate else None,
    )


def build_wrong_book(
    session: Session,
    student_id: str,
    *,
    homework_id: str | None = None,
    locale: str = "zh",
) -> WrongBookOut:
    hw_q = session.query(HomeworkEntry).filter(HomeworkEntry.student_id == student_id)
    if homework_id:
        hw_q = hw_q.filter(HomeworkEntry.id == homework_id)
    homeworks = hw_q.order_by(HomeworkEntry.finalized_at.desc()).all()

    items: list[WrongBookItemOut] = []
    candidates: list[WrongBookItemOut] = []

    for hw in homeworks:
        qs = (
            session.query(QuestionEntity)
            .filter(
                QuestionEntity.homework_id == hw.id,
                QuestionEntity.student_id == student_id,
            )
            .order_by(QuestionEntity.ordinal)
            .all()
        )
        for q in qs:
            if q.is_wrong is True:
                items.append(_to_wrong_item(q, hw, session=session, locale=locale))
            elif is_wrong_book_candidate(q):
                candidates.append(_to_wrong_item(q, hw, session=session, locale=locale, as_candidate=True))

    return WrongBookOut(
        items=items,
        candidates=candidates,
        count=len(items),
        candidate_count=len(candidates),
    )
