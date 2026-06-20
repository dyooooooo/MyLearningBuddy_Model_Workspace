from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.schemas.common import HomeworkOut
from app.services.ingest_homework import get_homework_detail, list_homework

router = APIRouter(prefix="/api/v1/students", tags=["homework"])


@router.get("/{student_id}/homework", response_model=list[HomeworkOut])
def get_homework_list(student_id: str, session: Session = Depends(db_session)) -> list[HomeworkOut]:
    return list_homework(session, student_id)


@router.get("/{student_id}/homework/{homework_id}", response_model=HomeworkOut)
def get_homework_one(
    student_id: str,
    homework_id: str,
    session: Session = Depends(db_session),
) -> HomeworkOut:
    row = get_homework_detail(session, student_id, homework_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Homework not found")
    return row
