from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

from langgraph.graph import END, StateGraph

from app.config import settings
from app.db.models import GraphRun
from app.db.session import get_session_factory
from app.graph import nodes
from app.graph.state import GraphState
from app.jobs.queue import schedule_graph_run
from app.util.json_util import dumps

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wrap(fn: Callable[[GraphState, object], GraphState]):
    def node(state: GraphState) -> GraphState:
        factory = get_session_factory()
        with factory() as session:
            try:
                out = fn(state, session)
                session.commit()
                return out
            except Exception as e:
                session.rollback()
                logger.exception("graph node %s failed", fn.__name__)
                return {
                    **state,
                    "status": "failed",
                    "errors": [str(e)],
                    "steps": [{"node": fn.__name__, "ts": _now_iso(), "detail": "error"}],
                }

    return node


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("load_homework", _wrap(nodes.load_homework))
    g.add_node("rag_validate", _wrap(nodes.rag_validate))
    g.add_node("eval_agent", _wrap(nodes.eval_agent_node))
    g.add_node("observer", _wrap(nodes.observer))
    g.add_node("aplus_aggregate", _wrap(nodes.aplus_aggregate_node))
    g.add_node("diagnosis", _wrap(nodes.diagnosis))
    g.add_node("planning", _wrap(nodes.planning))
    g.add_node("finalize_run", _wrap(nodes.finalize_run))
    g.set_entry_point("load_homework")
    g.add_edge("load_homework", "rag_validate")
    g.add_edge("rag_validate", "eval_agent")
    g.add_edge("eval_agent", "observer")
    g.add_edge("observer", "aplus_aggregate")
    g.add_edge("aplus_aggregate", "diagnosis")
    g.add_edge("diagnosis", "planning")
    g.add_edge("planning", "finalize_run")
    g.add_edge("finalize_run", END)
    return g.compile()


def run_graph_sync(
    student_id: str,
    homework_id: str,
    run_id: str | None = None,
    *,
    subject: str = "math",
    grade_band: str = "hs",
) -> str:
    rid = run_id or str(uuid.uuid4())
    factory = get_session_factory()
    with factory() as session:
        run = GraphRun(
            id=rid,
            student_id=student_id,
            homework_id=homework_id,
            subject=subject,
            grade_band=grade_band,
            status="running",
            started_at=_now_iso(),
            steps_json="[]",
        )
        session.merge(run)
        session.commit()

    initial: GraphState = {
        "student_id": student_id,
        "homework_id": homework_id,
        "run_id": rid,
        "subject": subject,
        "grade_band": grade_band,
        "status": "running",
        "steps": [],
        "errors": [],
    }
    graph = build_graph()
    try:
        final = graph.invoke(initial)
        logger.info("graph run %s finished status=%s", rid, final.get("status"))
    except Exception as e:
        logger.exception("graph run %s failed", rid)
        with factory() as session:
            run = session.get(GraphRun, rid)
            if run:
                run.status = "failed"
                run.error = str(e)
                run.finished_at = _now_iso()
                session.commit()
    return rid


__all__ = ["build_graph", "run_graph_sync", "schedule_graph_run"]
