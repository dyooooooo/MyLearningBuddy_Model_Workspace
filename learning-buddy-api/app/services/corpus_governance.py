from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CorpusEdge, CorpusImportJob, CorpusInboxItem, CorpusNode, CorpusScoutRun, CorpusTagLink
from app.rag.corpus.loaders import kp_labels_en_from_docs, kp_labels_from_docs, load_all_corpus, sync_docs_to_graph
from app.rag.corpus.manifest import count_by_type, write_manifest
from app.rag.vector.factory import get_staging_vector_store, get_vector_store
from app.services.corpus_locale import LocaleValidationError, assert_publish_locale
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

# 严格审核流：draft → review → published（发布走 publish_node，不可直接 set published）
ALLOWED_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"review", "rejected", "archived"},
    "review": {"draft", "rejected", "archived"},
    "published": {"archived", "review"},
    "archived": {"draft"},
    "rejected": {"draft"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StatusTransitionError(ValueError):
    pass


class PublishGuardError(ValueError):
    pass


def assert_status_transition(current: str, target: str) -> None:
    if target == "published":
        raise StatusTransitionError("use POST /nodes/{id}/publish after review approval")
    allowed = ALLOWED_STATUS_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise StatusTransitionError(f"cannot transition {current} → {target}")


def assert_publishable(node: CorpusNode) -> None:
    if node.status != "review":
        raise PublishGuardError(f"node {node.id} must be in review status before publish (current={node.status})")


def apply_status_transition(session: Session, node: CorpusNode, target: str) -> None:
    current = node.status or "draft"
    if target == current:
        return
    assert_status_transition(current, target)
    if current == "published" and target != "published":
        remove_nodes_from_vector_store([node.id])
        meta = loads(node.meta_json, {})
        meta.pop("vector_indexed_at", None)
        meta.pop("vector_collection", None)
        meta.pop("published_at", None)
        node.meta_json = dumps(meta)
    node.status = target
    node.updated_at = _now_iso()


def submit_for_review(session: Session, node_id: str) -> CorpusNode:
    node = session.get(CorpusNode, node_id)
    if node is None:
        raise ValueError(f"node not found: {node_id}")
    assert_publish_locale(
        node.subject,
        label=node.label or "",
        summary=node.summary or "",
        full_text=node.full_text or "",
    )
    apply_status_transition(session, node, "review")
    session.flush()
    return node


def remove_nodes_from_vector_store(node_ids: list[str]) -> None:
    if not node_ids:
        return
    for store in (get_vector_store(), get_staging_vector_store()):
        try:
            if hasattr(store, "delete_by_node_ids"):
                store.delete_by_node_ids(node_ids)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("vector delete failed store=%s", store)


def clear_vector_stores() -> None:
    get_vector_store().recreate_collection()
    get_staging_vector_store().recreate_collection()
    logger.info("Milvus production + staging collections recreated (empty)")


def wipe_corpus_graph(session: Session) -> dict[str, int]:
    """Delete all PG corpus graph rows (nodes, edges, tags)."""
    counts = {
        "tag_links": session.query(CorpusTagLink).delete(synchronize_session=False),
        "edges": session.query(CorpusEdge).delete(synchronize_session=False),
        "nodes": session.query(CorpusNode).delete(synchronize_session=False),
        "import_jobs": session.query(CorpusImportJob).delete(synchronize_session=False),
        "inbox": session.query(CorpusInboxItem).delete(synchronize_session=False),
        "scout_runs": session.query(CorpusScoutRun).delete(synchronize_session=False),
    }
    session.flush()
    return counts


def reset_corpus_workspace(session: Session, *, reseed: bool = False) -> dict:
    """
    清空向量库 + PG 语料图。默认保持空库；仅当 reseed=True 时从 data/ 以 draft 重载 PG（仍不写 Milvus）。
    """
    from app.rag.corpus.pipeline import reseed_graph_from_files

    clear_vector_stores()
    wiped = wipe_corpus_graph(session)
    session.commit()
    n, s, q = 0, 0, 0
    if reseed:
        n, s, q = reseed_graph_from_files(session, status="draft")
        session.commit()
    else:
        write_manifest(doc_count=0, syllabus_count=0, bank_count=0)
        session.commit()
    return {
        "wiped": wiped,
        "reseeded_nodes": n,
        "syllabus_count": s,
        "question_bank_count": q,
        "default_status": "draft" if reseed else "empty",
        "workflow": "draft → review → publish",
    }
