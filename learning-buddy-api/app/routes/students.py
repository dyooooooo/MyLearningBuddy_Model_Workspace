from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Student
from app.db.session import db_session
from app.schemas.common import StudentOut

router = APIRouter(prefix="/api/v1/students", tags=["students"])


class CreateStudentRequest(BaseModel):
    id: str
    label: str = ""


@router.post("", response_model=StudentOut)
def create_student(body: CreateStudentRequest, session: Session = Depends(db_session)) -> StudentOut:
    if session.get(Student, body.id):
        raise HTTPException(status_code=409, detail=f"Student exists: {body.id}")
    row = Student(id=body.id, label=body.label or body.id)
    session.add(row)
    session.flush()
    return StudentOut(
        id=row.id,
        label=row.label,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


from app.services.monetization import effective_tier, tier_features


class StudentTierOut(BaseModel):
    student_id: str
    tier: str
    features: list[str]
    trial_expires_at: str = ""


@router.get("/{student_id}/tier", response_model=StudentTierOut)
def get_student_tier(student_id: str, session: Session = Depends(db_session)) -> StudentTierOut:
    row = session.get(Student, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Student not found")
    tier = effective_tier(row)
    session.merge(row)
    session.flush()
    return StudentTierOut(
        student_id=row.id,
        tier=tier,
        features=sorted(tier_features(tier)),
        trial_expires_at=row.trial_expires_at or "",
    )


@router.get("/{student_id}", response_model=StudentOut)
def get_student(student_id: str, session: Session = Depends(db_session)) -> StudentOut:
    row = session.get(Student, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return StudentOut(
        id=row.id,
        label=row.label,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )
