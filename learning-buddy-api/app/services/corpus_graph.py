from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import CorpusEdge, CorpusNode, CorpusTagLink
from app.rag.corpus.schema import EDGE_PAIRS
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_node(
    session: Session,
    *,
    node_id: str,
    node_type: str,
    subject: str,
    grade_band: str,
    label: str = "",
    summary: str = "",
    full_text: str = "",
    tags_json: list[str] | None = None,
    meta_json: dict | None = None,
    status: str = "published",
) -> CorpusNode:
    row = session.get(CorpusNode, node_id)
    if row is None:
        row = (
            session.query(CorpusNode)
            .filter(CorpusNode.id == node_id)
            .first()
        )
    if row is None:
        row = CorpusNode(
            id=node_id,
            node_type=node_type,
            subject=subject,
            grade_band=grade_band,
        )
        session.add(row)
    row.label = label or row.label or node_id
    row.summary = summary or row.summary
    row.full_text = full_text or row.full_text
    row.tags_json = dumps(tags_json or loads(row.tags_json, []))
    row.meta_json = dumps(meta_json or loads(row.meta_json, {}))
    row.status = status
    row.updated_at = _now_iso()
    return row


def add_edge(
    session: Session,
    *,
    from_id: str,
    to_id: str,
    rel_type: str,
    source: str = "system",
    weight: float = 1.0,
    bidirectional: bool = True,
) -> None:
    if not from_id or not to_id:
        return
    _add_one_edge(session, from_id=from_id, to_id=to_id, rel_type=rel_type, source=source, weight=weight)
    if bidirectional:
        inverse = EDGE_PAIRS.get(rel_type, rel_type)
        if inverse != rel_type:
            _add_one_edge(session, from_id=to_id, to_id=from_id, rel_type=inverse, source=source, weight=weight)


def _add_one_edge(
    session: Session,
    *,
    from_id: str,
    to_id: str,
    rel_type: str,
    source: str,
    weight: float,
) -> None:
    existing = (
        session.query(CorpusEdge)
        .filter(
            CorpusEdge.from_id == from_id,
            CorpusEdge.to_id == to_id,
            CorpusEdge.rel_type == rel_type,
        )
        .first()
    )
    if existing:
        existing.weight = weight
        existing.source = source
        return
    session.add(
        CorpusEdge(
            from_id=from_id,
            to_id=to_id,
            rel_type=rel_type,
            weight=weight,
            source=source,
            created_at=_now_iso(),
        )
    )


def link_tag(session: Session, node_id: str, tag_id: str, source: str = "system") -> None:
    upsert_node(session, node_id=tag_id, node_type="tag", subject="*", grade_band="*", label=tag_id)
    add_edge(session, from_id=node_id, to_id=tag_id, rel_type="tagged_with", source=source)
    existing = (
        session.query(CorpusTagLink)
        .filter(CorpusTagLink.node_id == node_id, CorpusTagLink.tag_id == tag_id)
        .first()
    )
    if not existing:
        session.add(CorpusTagLink(node_id=node_id, tag_id=tag_id, created_at=_now_iso()))


def expand_neighbors(
    session: Session,
    node_ids: list[str],
    *,
    rel_types: set[str] | None = None,
    max_hops: int = 2,
    limit: int = 50,
) -> list[CorpusNode]:
    seen: set[str] = set(node_ids)
    frontier = list(node_ids)
    collected: dict[str, CorpusNode] = {}

    for _ in range(max_hops):
        if not frontier:
            break
        q = session.query(CorpusEdge).filter(CorpusEdge.from_id.in_(frontier))
        if rel_types:
            q = q.filter(CorpusEdge.rel_type.in_(rel_types))
        edges = q.limit(limit * 4).all()
        next_frontier: list[str] = []
        for e in edges:
            if e.to_id not in seen:
                seen.add(e.to_id)
                next_frontier.append(e.to_id)
        for nid in next_frontier:
            node = session.get(CorpusNode, nid)
            if node and node.status == "published":
                collected[nid] = node
        frontier = next_frontier
        if len(collected) >= limit:
            break

    return list(collected.values())[:limit]


def link_question_from_ingest(
    session: Session,
    *,
    question_node_id: str,
    kp_ids: list[str],
    method_ids: list[str] | None = None,
    shortcut_ids: list[str] | None = None,
    subject: str,
    grade_band: str,
    source: str = "ingest",
) -> None:
    upsert_node(
        session,
        node_id=question_node_id,
        node_type="question",
        subject=subject,
        grade_band=grade_band,
    )
    for kp in kp_ids:
        if not kp:
            continue
        add_edge(session, from_id=question_node_id, to_id=kp, rel_type="covers_kp", source=source)
    for mid in method_ids or []:
        add_edge(session, from_id=question_node_id, to_id=mid, rel_type="applies_method", source=source)
    for sid in shortcut_ids or []:
        add_edge(session, from_id=question_node_id, to_id=sid, rel_type="uses_shortcut_q", source=source)


def schedule_edge_completion(session: Session, node_id: str, subject: str, grade_band: str) -> None:
    """Mark node for async cross-type edge completion (worker picks up via Redis)."""
    upsert_node(
        session,
        node_id=node_id,
        node_type="question",
        subject=subject,
        grade_band=grade_band,
        status="pending_edges",
    )
