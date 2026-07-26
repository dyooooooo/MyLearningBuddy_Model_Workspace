from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.agents.eval_runner import run_eval_on_homework
from app.db.models import HomeworkEntry, QuestionEntity
from app.db.session import db_session
from app.schemas.common import HomeworkEvalTracesOut, HomeworkOut, PatchHomeworkRequest
from app.services.eval_traces import build_homework_eval_traces
from app.services.ingest_homework import (
    delete_homework,
    get_homework_detail,
    homework_to_out,
    list_homework,
    patch_homework,
    question_to_out,
)
from app.util.json_util import loads
from app.util.locale import locale_from_request

router = APIRouter(prefix="/api/v1/students", tags=["homework"])


def _homework_with_locale(session: Session, entry: HomeworkEntry, locale: str) -> HomeworkOut:
    qs = (
        session.query(QuestionEntity)
        .filter(QuestionEntity.homework_id == entry.id, QuestionEntity.student_id == entry.student_id)
        .order_by(QuestionEntity.ordinal)
        .all()
    )
    return homework_to_out(entry, [question_to_out(q, session=session, locale=locale) for q in qs])


@router.get("/{student_id}/homework", response_model=list[HomeworkOut])
def get_homework_list(
    student_id: str,
    request: Request,
    session: Session = Depends(db_session),
) -> list[HomeworkOut]:
    locale = locale_from_request(request)
    rows = (
        session.query(HomeworkEntry)
        .filter(HomeworkEntry.student_id == student_id)
        .order_by(HomeworkEntry.finalized_at.desc())
        .all()
    )
    return [_homework_with_locale(session, entry, locale) for entry in rows]


@router.get("/{student_id}/homework/{homework_id}", response_model=HomeworkOut)
def get_homework_one(
    student_id: str,
    homework_id: str,
    request: Request,
    session: Session = Depends(db_session),
) -> HomeworkOut:
    locale = locale_from_request(request)
    entry = session.get(HomeworkEntry, homework_id)
    if entry is None or entry.student_id != student_id:
        raise HTTPException(status_code=404, detail="Homework not found")
    return _homework_with_locale(session, entry, locale)


@router.patch("/{student_id}/homework/{homework_id}", response_model=HomeworkOut)
def patch_homework_route(
    student_id: str,
    homework_id: str,
    body: PatchHomeworkRequest,
    request: Request,
    session: Session = Depends(db_session),
) -> HomeworkOut:
    row = patch_homework(
        session,
        student_id,
        homework_id,
        meta=body.meta,
        finalized_at=body.finalized_at,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Homework not found")
    locale = locale_from_request(request)
    entry = session.get(HomeworkEntry, homework_id)
    return _homework_with_locale(session, entry, locale)


@router.delete("/{student_id}/homework/{homework_id}")
def delete_homework_route(
    student_id: str,
    homework_id: str,
    session: Session = Depends(db_session),
) -> dict:
    if not delete_homework(session, student_id, homework_id):
        raise HTTPException(status_code=404, detail="Homework not found")
    session.commit()
    return {"deleted": homework_id}


@router.get("/{student_id}/homework/{homework_id}/eval-traces", response_model=HomeworkEvalTracesOut)
def get_homework_eval_traces(
    student_id: str,
    homework_id: str,
    session: Session = Depends(db_session),
) -> HomeworkEvalTracesOut:
    out = build_homework_eval_traces(session, student_id, homework_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Homework not found")
    return out


@router.post("/{student_id}/homework/{homework_id}/re-eval")
def re_eval_homework(
    student_id: str,
    homework_id: str,
    session: Session = Depends(db_session),
) -> dict:
    entry = session.get(HomeworkEntry, homework_id)
    if entry is None or entry.student_id != student_id:
        raise HTTPException(status_code=404, detail="Homework not found")
    meta = loads(entry.meta_json, {})
    subject = entry.subject or meta.get("subject", "math")
    grade_band = entry.grade_band or meta.get("grade_band", "hs")
    n = run_eval_on_homework(
        session,
        student_id,
        homework_id,
        subject,
        grade_band,
        clear_manual_override=False,
    )
    from app.services.aplus_aggregate import run_aplus_aggregate_for_homework

    run_aplus_aggregate_for_homework(session, student_id, homework_id, subject, grade_band)
    session.commit()
    return {"eval_updated": n, "homework_id": homework_id}
