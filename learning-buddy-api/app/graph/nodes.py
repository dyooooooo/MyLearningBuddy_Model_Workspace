from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.agents.diagnosis_planning import run_diagnosis, run_planning
from app.agents.eval_runner import run_eval_on_homework
from app.agents.observer_agent import run_observer
from app.agents.rag_validate import run_rag_validate_on_homework
from app.db.models import GraphRun, HomeworkEntry, QuestionEntity
from app.graph.state import GraphState
from app.services.aplus_aggregate import run_aplus_aggregate_for_homework
from app.util.json_util import dumps


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _step(name: str, detail: str = "") -> dict:
    return {"node": name, "ts": _now_iso(), "detail": detail}


logger = logging.getLogger(__name__)


def load_homework(state: GraphState, session: Session) -> GraphState:
    entry = session.get(HomeworkEntry, state["homework_id"])
    if entry is None:
        return {
            **state,
            "status": "failed",
            "errors": [f"homework not found: {state['homework_id']}"],
            "steps": [_step("load_homework", "missing")],
        }
    n_q = (
        session.query(QuestionEntity)
        .filter(QuestionEntity.homework_id == state["homework_id"])
        .count()
    )
    return {
        **state,
        "subject": entry.subject or state.get("subject", "math"),
        "grade_band": entry.grade_band or state.get("grade_band", "hs"),
        "steps": [_step("load_homework", f"questions={n_q}")],
    }


def rag_validate(state: GraphState, session: Session) -> GraphState:
    if state.get("status") == "failed":
        return state
    n = run_rag_validate_on_homework(
        session,
        state["student_id"],
        state["homework_id"],
        state["subject"],
        state["grade_band"],
    )
    return {**state, "steps": [_step("rag_validate", f"kp_updated={n}")]}


def eval_agent_node(state: GraphState, session: Session) -> GraphState:
    """Eval is critical — run even if rag_validate marked state failed."""
    stats: dict = {}
    n = run_eval_on_homework(
        session,
        state["student_id"],
        state["homework_id"],
        state["subject"],
        state["grade_band"],
        stats=stats,
    )
    detail = (
        f"eval_updated={n} traces={stats.get('traces', 0)} "
        f"avg_ms={stats.get('avg_ms', 0)} log_file={stats.get('log_file', '')}"
    )
    steps = list(state.get("steps") or [])
    steps.append(_step("eval_agent", detail))
    return {**state, "status": "running", "steps": steps}


def observer(state: GraphState, session: Session) -> GraphState:
    if state.get("status") == "failed":
        return state
    detail = run_observer(
        session,
        state["student_id"],
        state["homework_id"],
        state["subject"],
        state["grade_band"],
    )
    return {**state, "steps": [_step("observer", detail)]}


def aplus_aggregate_node(state: GraphState, session: Session) -> GraphState:
    if state.get("status") == "failed":
        return state
    from app.db.models import Student
    from app.services.monetization import has_feature

    st = session.get(Student, state["student_id"])
    if st and not has_feature(st, "aplus"):
        return {**state, "steps": [_step("aplus_aggregate", "skipped:tier")]}
    n = run_aplus_aggregate_for_homework(
        session,
        state["student_id"],
        state["homework_id"],
        state["subject"],
        state["grade_band"],
    )
    return {**state, "steps": [_step("aplus_aggregate", f"kp_updated={n}")]}


def diagnosis(state: GraphState, session: Session) -> GraphState:
    if state.get("status") == "failed":
        return state
    detail = run_diagnosis(session, state["student_id"], state["subject"], state["grade_band"])
    return {**state, "steps": [_step("diagnosis", detail)]}


def planning(state: GraphState, session: Session) -> GraphState:
    if state.get("status") == "failed":
        return state
    detail = run_planning(session, state["student_id"], state["subject"], state["grade_band"])
    return {**state, "steps": [_step("planning", detail)]}


def finalize_run(state: GraphState, session: Session) -> GraphState:
    run = session.get(GraphRun, state["run_id"])
    final_status = "failed" if state.get("status") == "failed" or state.get("errors") else "succeeded"
    final_step = _step("finalize_run", final_status)
    steps = list(state.get("steps", [])) + [final_step]
    if run:
        run.status = final_status
        run.finished_at = _now_iso()
        run.steps_json = dumps(steps)
        if state.get("errors"):
            run.error = "; ".join(state["errors"])
        session.merge(run)
        session.flush()
    return {**state, "status": final_status, "steps": [final_step]}
