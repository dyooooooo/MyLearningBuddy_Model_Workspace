from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.schemas.common import HomeworkOut, IngestEventsRequest, IngestHomeworkRequest
from app.services.ingest_homework import ingest_events, ingest_homework

router = APIRouter(prefix="/api/v1/students", tags=["ingest"])


@router.post("/{student_id}/ingest/homework", response_model=HomeworkOut)
def post_ingest_homework(
    student_id: str,
    body: IngestHomeworkRequest,
    session: Session = Depends(db_session),
) -> HomeworkOut:
    return ingest_homework(session, student_id, body)


@router.post("/{student_id}/ingest/events")
def post_ingest_events(
    student_id: str,
    body: IngestEventsRequest,
    session: Session = Depends(db_session),
) -> dict:
    n = ingest_events(session, student_id, body.events)
    return {"ingested": n}
