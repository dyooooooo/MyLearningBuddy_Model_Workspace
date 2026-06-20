from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import GraphRun
from app.db.session import db_session
from app.util.json_util import loads

router = APIRouter(prefix="/api/v1/students", tags=["graph-runs"])


def _run_out(r: GraphRun) -> dict:
    return {
        "id": r.id,
        "student_id": r.student_id,
        "homework_id": r.homework_id,
        "status": r.status,
        "steps": loads(r.steps_json, []),
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "error": r.error or None,
    }


@router.get("/{student_id}/graph-runs")
def list_graph_runs(student_id: str, session: Session = Depends(db_session)) -> list[dict]:
    rows = (
        session.query(GraphRun)
        .filter(GraphRun.student_id == student_id)
        .order_by(GraphRun.started_at.desc())
        .limit(50)
        .all()
    )
    return [_run_out(r) for r in rows]


@router.get("/{student_id}/graph-runs/{run_id}")
def get_graph_run(
    student_id: str, run_id: str, session: Session = Depends(db_session)
) -> dict:
    row = session.get(GraphRun, run_id)
    if row is None or row.student_id != student_id:
        raise HTTPException(status_code=404, detail="Graph run not found")
    return _run_out(row)
