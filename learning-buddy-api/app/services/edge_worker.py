from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.rag.retriever import map_question_to_kp, retrieve_eval_context
from app.services.corpus_graph import add_edge, upsert_node

logger = logging.getLogger(__name__)


def complete_edges_for_question(
    session: Session,
    *,
    node_id: str,
    subject: str,
    grade_band: str,
) -> int:
    """Async worker: enrich cross-type edges from RAG graph expand."""
    from app.db.models import QuestionEntity

    q = session.get(QuestionEntity, node_id)
    if q is None or not (q.print_text or "").strip():
        return 0
    ctx = retrieve_eval_context(q.print_text, subject=subject, grade_band=grade_band)
    count = 0
    for kp in ctx.kps:
        kid = kp.get("meta", {}).get("kp_id") or kp.get("id")
        if kid:
            add_edge(session, from_id=node_id, to_id=kid, rel_type="covers_kp", source="edge_worker")
            count += 1
    for m in ctx.methods:
        add_edge(session, from_id=node_id, to_id=m["id"], rel_type="applies_method", source="edge_worker")
        count += 1
    for s in ctx.shortcuts:
        add_edge(session, from_id=node_id, to_id=s["id"], rel_type="uses_shortcut_q", source="edge_worker")
        count += 1
    for c in ctx.chunks:
        add_edge(session, from_id=node_id, to_id=c["id"], rel_type="references_chunk", source="edge_worker")
        count += 1
    upsert_node(
        session,
        node_id=node_id,
        node_type="question",
        subject=subject,
        grade_band=grade_band,
        status="published",
    )
    session.flush()
    return count
