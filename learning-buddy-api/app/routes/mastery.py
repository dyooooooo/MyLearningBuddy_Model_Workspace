from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.schemas.common import MasteryViewOut, PatchMasteryRequest, PostMasteryRequest
from app.services.aplus_aggregate import patch_mastery, recompute_student
from app.services.ingest_homework import ingest_events, list_mastery, merge_mastery_for_student, mastery_to_out
from app.schemas.common import MemoryEventIn
from app.db.models import MasteryView
from app.util.locale import locale_from_request

router = APIRouter(prefix="/api/v1/students", tags=["mastery"])


@router.get("/{student_id}/mastery", response_model=list[MasteryViewOut])
def get_mastery(
    student_id: str,
    request: Request,
    session: Session = Depends(db_session),
) -> list[MasteryViewOut]:
    locale = locale_from_request(request)
    rows = list_mastery(session, student_id)
    merged = merge_mastery_for_student(session, rows, locale=locale)
    return [MasteryViewOut.model_validate(m) for m in merged]


@router.post("/{student_id}/mastery", response_model=MasteryViewOut)
def post_mastery(
    student_id: str,
    body: PostMasteryRequest,
    request: Request,
    session: Session = Depends(db_session),
) -> MasteryViewOut:
    row = patch_mastery(session, student_id, body.kp_id, body.mastery)
    if row is None:
        raise HTTPException(status_code=500, detail="Failed to create mastery")
    if body.label:
        row.label = body.label
        session.merge(row)
    ingest_events(
        session,
        student_id,
        [
            MemoryEventIn(
                session_id="",
                ts=datetime.now(timezone.utc).isoformat(),
                type="MASTERY_OVERRIDE",
                kp_id=body.kp_id,
                payload={"mastery": body.mastery, "source": "manual_create"},
            )
        ],
    )
    session.commit()
    locale = locale_from_request(request)
    return MasteryViewOut.model_validate(mastery_to_out(row, session=session, locale=locale))


@router.patch("/{student_id}/mastery/{kp_id}", response_model=MasteryViewOut)
def patch_mastery_route(
    student_id: str,
    kp_id: str,
    body: PatchMasteryRequest,
    request: Request,
    session: Session = Depends(db_session),
) -> MasteryViewOut:
    row = patch_mastery(session, student_id, kp_id, body.mastery)
    if row is None:
        raise HTTPException(status_code=404, detail="Mastery not found")
    if body.weak is not None:
        row.weak = body.weak
    if body.confidence is not None:
        row.confidence = float(body.confidence)
    session.merge(row)
    ingest_events(
        session,
        student_id,
        [
            MemoryEventIn(
                session_id="",
                ts=datetime.now(timezone.utc).isoformat(),
                type="MASTERY_OVERRIDE",
                kp_id=kp_id,
                payload={"mastery": body.mastery, "weak": body.weak, "confidence": body.confidence},
            )
        ],
    )
    session.commit()
    locale = locale_from_request(request)
    return MasteryViewOut.model_validate(mastery_to_out(row, session=session, locale=locale))


@router.delete("/{student_id}/mastery/{kp_id}")
def delete_mastery_route(
    student_id: str,
    kp_id: str,
    session: Session = Depends(db_session),
) -> dict:
    row = session.get(MasteryView, {"student_id": student_id, "kp_id": kp_id})
    if row is None:
        raise HTTPException(status_code=404, detail="Mastery not found")
    session.delete(row)
    ingest_events(
        session,
        student_id,
        [
            MemoryEventIn(
                session_id="",
                ts=datetime.now(timezone.utc).isoformat(),
                type="MASTERY_REMOVED",
                kp_id=kp_id,
                payload={},
            )
        ],
    )
    session.commit()
    return {"deleted": kp_id}


@router.post("/{student_id}/mastery/recompute")
def recompute(student_id: str, session: Session = Depends(db_session)) -> dict:
    n = recompute_student(session, student_id)
    session.commit()
    return {"recomputed_kps": n}
