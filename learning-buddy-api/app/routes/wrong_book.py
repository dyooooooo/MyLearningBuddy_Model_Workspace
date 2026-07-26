from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.agents.eval_runner import (
    distinct_homework_ids_for_wrong_book,
    run_eval_on_homework_ids,
)
from app.db.models import HomeworkEntry
from app.db.session import db_session
from app.schemas.common import WrongBookOut
from app.services.aplus_aggregate import run_aplus_aggregate_for_homework, recompute_student
from app.services.wrong_book import build_wrong_book
from app.util.json_util import loads
from app.util.locale import locale_from_request

router = APIRouter(prefix="/api/v1/students", tags=["wrong-book"])


@router.get("/{student_id}/wrong-book", response_model=WrongBookOut)
def get_wrong_book(
    student_id: str,
    request: Request,
    homework_id: str | None = Query(default=None),
    session: Session = Depends(db_session),
) -> WrongBookOut:
    locale = locale_from_request(request)
    return build_wrong_book(session, student_id, homework_id=homework_id, locale=locale)


@router.post("/{student_id}/wrong-book/re-eval")
def wrong_book_re_eval(
    student_id: str,
    session: Session = Depends(db_session),
    homework_id: str | None = Query(default=None),
    all_homework: bool = Query(default=False, description="Re-eval every homework entry for student"),
    clear_manual: bool = Query(default=True, description="Clear manual_override before re-eval"),
) -> dict:
    if all_homework:
        hw_ids = [
            row.id
            for row in session.query(HomeworkEntry)
            .filter(HomeworkEntry.student_id == student_id)
            .order_by(HomeworkEntry.finalized_at.desc())
            .all()
        ]
    elif homework_id:
        entry = session.get(HomeworkEntry, homework_id)
        if entry is None or entry.student_id != student_id:
            raise HTTPException(status_code=404, detail="Homework not found")
        hw_ids = [homework_id]
    else:
        hw_ids = distinct_homework_ids_for_wrong_book(session, student_id)
        if not hw_ids:
            raise HTTPException(status_code=404, detail="No wrong-book homework to re-eval")

    totals = run_eval_on_homework_ids(
        session,
        student_id,
        hw_ids,
        clear_manual_override=clear_manual,
    )
    eval_updated = sum(totals.values())
    for hw_id in hw_ids:
        entry = session.get(HomeworkEntry, hw_id)
        if entry is None:
            continue
        meta = loads(entry.meta_json, {})
        subject = entry.subject or meta.get("subject", "math")
        grade_band = entry.grade_band or meta.get("grade_band", "hs")
        run_aplus_aggregate_for_homework(session, student_id, hw_id, subject, grade_band)
    recompute_student(session, student_id)
    session.commit()
    return {
        "homework_ids": hw_ids,
        "eval_updated_by_homework": totals,
        "eval_updated": eval_updated,
    }
