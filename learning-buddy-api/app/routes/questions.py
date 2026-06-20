from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.schemas.common import PatchQuestionRequest, QuestionOut
from app.services.ingest_homework import patch_question

router = APIRouter(prefix="/api/v1/students", tags=["questions"])


@router.patch("/{student_id}/questions/{question_id}", response_model=QuestionOut)
def patch_question_route(
    student_id: str,
    question_id: str,
    body: PatchQuestionRequest,
    session: Session = Depends(db_session),
) -> QuestionOut:
    row = patch_question(
        session,
        student_id,
        question_id,
        is_wrong=body.is_wrong,
        kp_tags=body.kp_tags,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return row
