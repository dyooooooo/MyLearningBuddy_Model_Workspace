from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.schemas.common import PatchQuestionRequest, QuestionOut
from app.services.ingest_homework import delete_question, patch_question, question_to_out
from app.db.models import QuestionEntity
from app.util.locale import locale_from_request

router = APIRouter(prefix="/api/v1/students", tags=["questions"])


@router.patch("/{student_id}/questions/{question_id}", response_model=QuestionOut)
def patch_question_route(
    student_id: str,
    question_id: str,
    body: PatchQuestionRequest,
    request: Request,
    session: Session = Depends(db_session),
) -> QuestionOut:
    row = patch_question(
        session,
        student_id,
        question_id,
        is_wrong=body.is_wrong,
        kp_tags=body.kp_tags,
        print_text=body.print_text,
        handwriting=body.handwriting,
        restore_agent=body.restore_agent,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    session.commit()
    locale = locale_from_request(request)
    q = session.get(QuestionEntity, question_id)
    return question_to_out(q, session=session, locale=locale)


@router.delete("/{student_id}/questions/{question_id}")
def delete_question_route(
    student_id: str,
    question_id: str,
    session: Session = Depends(db_session),
) -> dict:
    if not delete_question(session, student_id, question_id):
        raise HTTPException(status_code=404, detail="Question not found")
    session.commit()
    return {"deleted": question_id}
