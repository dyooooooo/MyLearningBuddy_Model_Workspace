from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.schemas.common import MasteryViewOut, PatchMasteryRequest
from app.services.aplus_aggregate import patch_mastery, recompute_student
from app.services.ingest_homework import list_mastery, mastery_to_out

router = APIRouter(prefix="/api/v1/students", tags=["mastery"])


@router.get("/{student_id}/mastery", response_model=list[MasteryViewOut])
def get_mastery(student_id: str, session: Session = Depends(db_session)) -> list[MasteryViewOut]:
    rows = list_mastery(session, student_id)
    return [MasteryViewOut.model_validate(mastery_to_out(r)) for r in rows]


@router.patch("/{student_id}/mastery/{kp_id}", response_model=MasteryViewOut)
def patch_mastery_route(
    student_id: str,
    kp_id: str,
    body: PatchMasteryRequest,
    session: Session = Depends(db_session),
) -> MasteryViewOut:
    from app.schemas.common import MemoryEventIn
    from app.services.ingest_homework import ingest_events
    from datetime import datetime, timezone

    row = patch_mastery(session, student_id, kp_id, body.mastery)
    if row is None:
        raise HTTPException(status_code=404, detail="Mastery not found")
    ingest_events(
        session,
        student_id,
        [
            MemoryEventIn(
                session_id="",
                ts=datetime.now(timezone.utc).isoformat(),
                type="MASTERY_OVERRIDE",
                kp_id=kp_id,
                payload={"mastery": body.mastery},
            )
        ],
    )
    return MasteryViewOut.model_validate(mastery_to_out(row))


@router.post("/{student_id}/mastery/recompute")
def recompute(student_id: str, session: Session = Depends(db_session)) -> dict:
    n = recompute_student(session, student_id)
    return {"recomputed_kps": n}
