from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import GraphRun, HomeworkEntry, MasteryView, MemoryEvent, QuestionEntity, Student
from app.db.session import db_session
from app.auth import hash_token

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_STUDENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")


class CreateStudentRequest(BaseModel):
    id: str = Field(..., description="student id, e.g. student-01")
    label: str = ""
    grade_band: str = "hs"


class StudentOut(BaseModel):
    id: str
    label: str
    grade_band: str
    active: bool
    api_token: str | None = None


@router.get("/students", response_model=list[StudentOut])
def list_students(session: Session = Depends(db_session)) -> list[StudentOut]:
    rows = session.query(Student).order_by(Student.created_at).all()
    return [
        StudentOut(id=r.id, label=r.label, grade_band=r.grade_band, active=r.active)
        for r in rows
    ]


@router.post("/students", response_model=StudentOut)
def create_student(body: CreateStudentRequest, session: Session = Depends(db_session)) -> StudentOut:
    if not _STUDENT_ID_RE.match(body.id):
        raise HTTPException(status_code=400, detail="Invalid student id format")
    count = session.query(func.count(Student.id)).scalar() or 0
    if count >= settings.max_students:
        raise HTTPException(status_code=403, detail=f"Max students ({settings.max_students}) reached")
    if session.get(Student, body.id):
        raise HTTPException(status_code=409, detail="Student already exists")

    token = secrets.token_urlsafe(24)
    row = Student(
        id=body.id,
        label=body.label or body.id,
        grade_band=body.grade_band,
        active=True,
        api_token_hash=hash_token(token),
    )
    session.add(row)
    session.flush()
    return StudentOut(
        id=row.id,
        label=row.label,
        grade_band=row.grade_band,
        active=row.active,
        api_token=token,
    )


@router.delete("/students/{student_id}")
def delete_student(student_id: str, session: Session = Depends(db_session)) -> dict:
    row = session.get(Student, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Student not found")
    session.query(QuestionEntity).filter(QuestionEntity.student_id == student_id).delete()
    session.query(HomeworkEntry).filter(HomeworkEntry.student_id == student_id).delete()
    session.query(MemoryEvent).filter(MemoryEvent.student_id == student_id).delete()
    session.query(MasteryView).filter(MasteryView.student_id == student_id).delete()
    session.query(GraphRun).filter(GraphRun.student_id == student_id).delete()
    session.delete(row)
    session.flush()
    return {"deleted": student_id}


class SetTierRequest(BaseModel):
    tier: str = Field(..., description="trial | free | paid | b2b")
    org_id: str = ""


@router.patch("/students/{student_id}/tier")
def set_student_tier(
    student_id: str,
    body: SetTierRequest,
    session: Session = Depends(db_session),
) -> dict:
    row = session.get(Student, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Student not found")
    if body.tier not in ("trial", "free", "paid", "b2b"):
        raise HTTPException(status_code=400, detail="Invalid tier")
    row.tier = body.tier
    if body.org_id:
        row.org_id = body.org_id
    session.flush()
    return {"student_id": row.id, "tier": row.tier, "org_id": row.org_id or ""}


@router.get("/content-packs")
def list_content_packs() -> list[dict]:
    """B2B 按教材版本内容包清单（Method + Shortcut + Chunk）。"""
    return [
        {
            "pack_id": "math_hs_pep_2024",
            "label": "人教版高中数学 2024",
            "subjects": ["math"],
            "grade_band": "hs",
            "includes": ["methods", "shortcuts", "chunks"],
        },
        {
            "pack_id": "math_hs_generic",
            "label": "高中数学通用 Method/Shortcut",
            "subjects": ["math"],
            "grade_band": "hs",
            "includes": ["methods", "shortcuts"],
        },
    ]
