from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CorpusEdge, CorpusImportJob, CorpusInboxItem, CorpusNode, CorpusScoutRun, CorpusTagLink, QuestionEntity
from app.rag.corpus.manifest import manifest_stale
from app.rag.embedder import embed_texts
from app.rag.retriever import retrieve_eval_context
from app.rag.vector.factory import get_staging_vector_store, get_vector_store
from app.schemas.corpus import (
    BulkPublishRequest,
    CorpusEdgeCreate,
    CorpusNodeCreate,
    CorpusNodeOut,
    CorpusNodePatch,
    CorpusStatsOut,
    GraphOut,
    ImportJobOut,
    ImportPdfRequest,
    InboxItemOut,
    LinkEntryOut,
    MarkdownPatch,
    NodeLinksOut,
    PromoteQuestionRequest,
    PublishSubtreeOut,
    ReindexRequest,
    ReindexAsyncOut,
    ReindexJobStatusOut,
    ScoutRequest,
    ScoutRunOut,
    SearchPreviewRequest,
    SetStatusRequest,
    TreeNodeOut,
    WorkQueueOut,
)
from app.services.corpus_governance import (
    PublishGuardError,
    StatusTransitionError,
    apply_status_transition,
    reset_corpus_workspace,
    submit_for_review,
)
from app.services.corpus_locale import apply_corpus_locale
from app.services.corpus_locale import apply_corpus_locale
from app.services.corpus_import import create_import_job, process_import_job, run_import_chunk
from app.services.corpus_publish import embed_staging, publish_node, publish_nodes_bulk, upsert_draft_node
from app.services.corpus_tree import collect_subtree_ids, get_tree
from app.services.scout_agent import approve_inbox_item, create_scout_run, run_scout_sync
from app.util.json_util import dumps, loads
from app.util.page_sort import page_sort_key

logger = logging.getLogger(__name__)

LINK_GROUP_STRUCTURAL = {
    "contains_section",
    "contains_chunk",
    "in_textbook",
    "in_section",
    "part_of",
    "has_section",
}
LINK_GROUP_KP = {
    "defines_kp",
    "covers_kp",
    "applies_to_kp",
    "derived_from_kp",
    "prerequisite_of",
    "related_kp",
}
LINK_GROUP_TAGS = {"tagged_with", "has_tag", "tag"}
DEFAULT_HIDE_RELS = {"example_for", "references_chunk", "examined_by"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node_out(session: Session, node: CorpusNode) -> CorpusNodeOut:
    meta = loads(node.meta_json, {})
    bl_count = (
        session.query(func.count(CorpusEdge.id))
        .filter(CorpusEdge.to_id == node.id)
        .scalar()
        or 0
    )
    vi = bool(meta.get("vector_indexed_at"))
    return CorpusNodeOut(
        id=node.id,
        node_type=node.node_type,
        subject=node.subject,
        grade_band=node.grade_band,
        label=node.label,
        summary=node.summary,
        full_text=node.full_text,
        tags=loads(node.tags_json, []),
        links=meta.get("links") or {},
        meta=meta,
        status=node.status,
        updated_at=node.updated_at,
        backlink_count=int(bl_count),
        vector_indexed=vi,
    )


def get_stats(session: Session) -> CorpusStatsOut:
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in session.query(CorpusNode.status, func.count(CorpusNode.id)).group_by(CorpusNode.status):
        by_status[str(row[0])] = int(row[1])
    for row in session.query(CorpusNode.node_type, func.count(CorpusNode.id)).group_by(CorpusNode.node_type):
        by_type[str(row[0])] = int(row[1])

    prod_store = get_vector_store()
    prod = prod_store.count()
    staging = get_staging_vector_store().count()
    milvus_schema_ok = True
    if hasattr(prod_store, "schema_ok"):
        milvus_schema_ok = prod_store.schema_ok()  # type: ignore[attr-defined]
    published = session.query(CorpusNode).filter(CorpusNode.status == "published").count()
    unindexed = 0
    for n in session.query(CorpusNode).filter(CorpusNode.status == "published"):
        if not loads(n.meta_json, {}).get("vector_indexed_at"):
            unindexed += 1

    return CorpusStatsOut(
        nodes_by_status=by_status,
        nodes_by_type=by_type,
        milvus_production_count=prod,
        milvus_staging_count=staging,
        published_unindexed=unindexed,
        embed_model=settings.embed_model,
        manifest_stale=manifest_stale(),
        milvus_schema_ok=milvus_schema_ok,
    )


def list_nodes(
    session: Session,
    *,
    status: str | None = None,
    node_type: str | None = None,
    subject: str | None = None,
    grade_band: str | None = None,
    limit: int = 100,
) -> list[CorpusNodeOut]:
    q = session.query(CorpusNode)
    if status:
        q = q.filter(CorpusNode.status == status)
    if node_type:
        q = q.filter(CorpusNode.node_type == node_type)
    if subject:
        q = q.filter(CorpusNode.subject == subject)
    if grade_band:
        q = q.filter(CorpusNode.grade_band == grade_band)
    rows = q.order_by(CorpusNode.updated_at.desc()).limit(limit).all()
    return [_node_out(session, r) for r in rows]


def get_node(session: Session, node_id: str) -> CorpusNodeOut | None:
    node = session.get(CorpusNode, node_id)
    if node is None:
        return None
    return _node_out(session, node)


def create_node(session: Session, body: CorpusNodeCreate) -> CorpusNodeOut:
    row = upsert_draft_node(
        session,
        node_id=body.id,
        node_type=body.node_type,
        subject=body.subject,
        grade_band=body.grade_band,
        label=body.label,
        summary=body.summary,
        full_text=body.full_text,
        tags=body.tags,
        links=body.links,
        meta=body.meta,
        status="draft",
        source_channel="manual",
    )
    return _node_out(session, row)


def patch_node(session: Session, node_id: str, body: CorpusNodePatch) -> CorpusNodeOut | None:
    node = session.get(CorpusNode, node_id)
    if node is None:
        return None
    meta = loads(node.meta_json, {})
    if body.label is not None:
        node.label = body.label
    if body.summary is not None:
        node.summary = body.summary
    if body.full_text is not None:
        node.full_text = body.full_text
    if body.tags is not None:
        node.tags_json = dumps(body.tags)
    if body.links is not None:
        meta["links"] = body.links
    if body.meta is not None:
        meta.update(body.meta)
    if body.status is not None:
        try:
            apply_status_transition(session, node, body.status)
        except (StatusTransitionError, PublishGuardError) as e:
            raise ValueError(str(e)) from e
    label, summary, full_text, meta, _ = apply_corpus_locale(
        node.subject,
        label=node.label or "",
        summary=node.summary or "",
        full_text=node.full_text or "",
        meta=meta,
    )
    node.label = label
    node.summary = summary
    node.full_text = full_text
    node.meta_json = dumps(meta)
    node.updated_at = _now_iso()
    session.flush()
    return _node_out(session, node)


ACTIVE_IMPORT_STATUSES = ("uploaded", "converting", "parsed", "chunking")


def cancel_active_imports_for_subtree(session: Session, ids: list[str]) -> list[str]:
    """Cooperatively cancel any in-flight import job that writes into ``ids``.

    Returns the list of canceled job ids. The worker checks the cancel flag
    between pages and aborts, releasing the Postgres row locks that would
    otherwise block a subtree delete.
    """
    from app.services import pipeline_telemetry

    id_set = set(ids)
    jobs = (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.status.in_(ACTIVE_IMPORT_STATUSES))
        .all()
    )
    canceled: list[str] = []
    for job in jobs:
        if job.textbook_node_id and job.textbook_node_id in id_set:
            pipeline_telemetry.request_cancel(job.id)
            job.status = "canceled"
            job.error = "已取消（教材删除）"
            job.updated_at = _now_iso()
            canceled.append(job.id)
    if canceled:
        session.flush()
        session.commit()  # release so the worker sees the canceled status
        logger.info("delete: canceled %d active import job(s): %s", len(canceled), canceled)
    return canceled


def delete_import_job(session: Session, job_id: str) -> dict:
    """Hard-delete an import job row and stop any in-flight worker for it.

    Always sets the Redis cancel flag *before* deleting the row, and leaves that
    flag in place (TTL) so a busy worker can still see it. Do **not** clear the
    cancel key here — the worker clears it in its ``finally`` after aborting.
    """
    from app.clients.redis_client import purge_import_queue
    from app.services import pipeline_telemetry

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        # Still signal cancel + drop queue in case a worker is racing.
        pipeline_telemetry.request_cancel(job_id)
        purge_import_queue(job_id)
        return {"job_id": job_id, "deleted": False, "reason": "not_found"}

    was_active = job.status in ACTIVE_IMPORT_STATUSES
    # Always cancel first — worker may still be finishing after status drift.
    pipeline_telemetry.request_cancel(job_id)
    queue_removed = purge_import_queue(job_id)

    session.delete(job)
    session.flush()
    session.commit()

    # Keep cancel flag; only clear live telemetry so the dashboard drops the card.
    pipeline_telemetry.clear_job_live(job_id)
    logger.info(
        "import job deleted id=%s was_active=%s queue_removed=%d",
        job_id,
        was_active,
        queue_removed,
    )
    return {
        "job_id": job_id,
        "deleted": True,
        "was_active": was_active,
        "queue_removed": queue_removed,
    }


def delete_node(session: Session, node_id: str, *, cascade: bool = False) -> dict:
    import time as _time

    from sqlalchemy import text as _text

    from app.services.corpus_governance import remove_nodes_from_vector_store
    from app.services.corpus_tree import collect_subtree_ids

    node = session.get(CorpusNode, node_id)
    if node is None:
        return {"deleted": [], "count": 0}

    if cascade:
        ids = collect_subtree_ids(session, node_id)
        if node_id not in ids:
            ids.insert(0, node_id)
    else:
        child_edges = (
            session.query(CorpusEdge)
            .filter(CorpusEdge.from_id == node_id, CorpusEdge.rel_type.in_(("contains_section", "contains_chunk")))
            .limit(1)
            .count()
        )
        if child_edges > 0:
            raise ValueError("节点含子节点，请勾选「连同子树删除」或先删子节点")
        ids = [node_id]

    # Stop any import worker still writing into this subtree so its row locks
    # release; otherwise the batch DELETE below blocks and the UI times out.
    canceled_jobs = cancel_active_imports_for_subtree(session, ids)

    indexed = []
    for nid in ids:
        n = session.get(CorpusNode, nid)
        if n and loads(n.meta_json, {}).get("vector_indexed_at"):
            indexed.append(nid)
    if indexed:
        remove_nodes_from_vector_store(indexed)

    # Batch delete with a bounded lock wait + retry. If a canceled worker is
    # still mid-page, it releases within a couple seconds; we retry rather than
    # hang forever or fail instantly.
    attempts = 6 if canceled_jobs else 2
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            session.execute(_text("SET LOCAL lock_timeout = '3s'"))
            session.query(CorpusEdge).filter(
                (CorpusEdge.from_id.in_(ids)) | (CorpusEdge.to_id.in_(ids))
            ).delete(synchronize_session=False)
            session.query(CorpusTagLink).filter(CorpusTagLink.node_id.in_(ids)).delete(
                synchronize_session=False
            )
            session.query(CorpusNode).filter(CorpusNode.id.in_(ids)).delete(
                synchronize_session=False
            )
            session.flush()
            session.commit()
            return {"deleted": ids, "count": len(ids), "canceled_jobs": canceled_jobs}
        except Exception as exc:  # lock_timeout or transient conflict
            session.rollback()
            last_err = exc
            logger.warning("delete subtree attempt %d locked: %s", attempt + 1, exc)
            _time.sleep(1.5)
    raise ValueError(
        "删除被占用：导入任务仍在写入该教材。已发送取消请求，请等待几秒后重试删除。"
    ) from last_err


def get_backlinks(session: Session, node_id: str) -> list[CorpusNodeOut]:
    edges = session.query(CorpusEdge).filter(CorpusEdge.to_id == node_id).all()
    out: list[CorpusNodeOut] = []
    for e in edges:
        n = session.get(CorpusNode, e.from_id)
        if n:
            out.append(_node_out(session, n))
    return out


def add_node_edge(session: Session, node_id: str, body: CorpusEdgeCreate) -> None:
    add_edge(
        session,
        from_id=node_id,
        to_id=body.to_id,
        rel_type=body.rel_type,
        source="studio",
        weight=body.weight,
    )


def search_preview(body: SearchPreviewRequest) -> dict:
    if body.collection == "staging":
        from app.rag.vector.factory import get_staging_vector_store

        store = get_staging_vector_store()
        qv = embed_texts([body.query])[0]
        raw = store.search(
            qv,
            subject=body.subject,
            grade_band=body.grade_band,
            top_k=body.top_k,
            status_filter=None,
        )
        return {
            "collection": "staging",
            "hits": [h.__dict__ for h in raw],
        }
    ctx = retrieve_eval_context(body.query, subject=body.subject, grade_band=body.grade_band)
    return {"collection": "production", **ctx.to_dict()}


def reindex_corpus(session: Session, body: ReindexRequest) -> dict:
    if body.force:
        return reset_corpus_workspace(session, reseed=False)
    if body.node_ids:
        review_ids = []
        for nid in body.node_ids:
            node = session.get(CorpusNode, nid)
            if node and node.status == "review":
                review_ids.append(nid)
        n = publish_nodes_bulk(session, review_ids)
        return {"mode": "bulk_publish", "count": n, "skipped_non_review": len(body.node_ids) - len(review_ids)}
    review_nodes = session.query(CorpusNode).filter(CorpusNode.status == "review").all()
    ids = [n.id for n in review_nodes]
    n = publish_nodes_bulk(session, ids)
    return {"mode": "publish_all_review", "count": n, "queued": len(ids)}


def import_pdf_start(session: Session, body: ImportPdfRequest) -> ImportJobOut:
    job = create_import_job(
        session,
        minio_key=body.minio_key,
        subject=body.subject,
        grade_band=body.grade_band,
        label=body.label,
        textbook_id=body.textbook_id,
        source_channel=body.source_channel,
        parse_engine=body.parse_engine,
        page_limit=body.page_limit,
        page_ranges=body.page_ranges,
        edition_id=body.edition_id,
    )
    logger.info(
        "import pdf queued id=%s label=%r minio_key=%s",
        job.id,
        job.label,
        job.minio_key,
    )
    return _job_out(job, session)


class ImportJobConflictError(RuntimeError):
    """Extend rejected: the job is running or already has a pending extend."""


def import_job_extend(session: Session, job_id: str, page_ranges: str) -> ImportJobOut:
    """Queue MinerU segment append on an existing job (AI 分段导入)."""
    from app.util.json_util import dumps, loads

    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    pr = (page_ranges or "").strip()
    if not pr:
        raise ValueError("page_ranges is required")
    if job.status in ("canceled",):
        raise ValueError(f"cannot extend job in status={job.status}")
    meta = loads(job.meta_json, {})
    if job.status in ACTIVE_IMPORT_STATUSES:
        raise ImportJobConflictError(
            f"job is running (status={job.status}); wait for it to finish before extending"
        )
    pending = str(meta.get("pending_page_ranges") or "").strip()
    if pending:
        raise ImportJobConflictError(
            f"job already has a pending extend page_ranges={pending!r}; refusing to overwrite it"
        )
    # Record the real status so worker logs can show it after we reset below.
    meta["prev_status"] = job.status
    meta["pending_page_ranges"] = pr
    meta["extend_mode"] = True
    job.meta_json = dumps(meta)
    # Allow worker to pick up even if previously chunked/parsed
    job.status = "uploaded"
    job.error = ""
    job.updated_at = datetime.now(timezone.utc).isoformat()
    session.flush()
    logger.info("import extend queued id=%s page_ranges=%s", job_id, pr)
    return _job_out(job, session)


def _job_meta(session: Session | None, job: CorpusImportJob) -> dict:
    meta = loads(job.meta_json, {})
    if session and job.textbook_node_id and job.status in ("chunked", "review"):
        meta["nodes_missing"] = session.get(CorpusNode, job.textbook_node_id) is None
    return meta


def _job_out(job: CorpusImportJob, session: Session | None = None) -> ImportJobOut:
    return ImportJobOut(
        id=job.id,
        status=job.status,
        source_channel=job.source_channel,
        minio_key=job.minio_key,
        markdown=job.markdown,
        textbook_node_id=job.textbook_node_id,
        subject=job.subject,
        grade_band=job.grade_band,
        label=job.label,
        error=job.error,
        meta=_job_meta(session, job),
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def list_import_jobs(session: Session, limit: int = 50) -> list[ImportJobOut]:
    rows = session.query(CorpusImportJob).order_by(CorpusImportJob.created_at.desc()).limit(limit).all()
    return [_job_out(r, session) for r in rows]


def get_import_job(session: Session, job_id: str) -> ImportJobOut | None:
    job = session.get(CorpusImportJob, job_id)
    return _job_out(job, session) if job else None


def patch_job_markdown(session: Session, job_id: str, body: MarkdownPatch) -> ImportJobOut:
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError("job not found")
    job.markdown = body.markdown
    job.status = "parsed"
    job.updated_at = _now_iso()
    session.flush()
    return _job_out(job, session)
    run = create_scout_run(
        session,
        query=body.query,
        url=body.url,
        subject=body.subject,
        grade_band=body.grade_band,
    )
    return _scout_out(run)


def _scout_out(run: CorpusScoutRun) -> ScoutRunOut:
    return ScoutRunOut(
        id=run.id,
        query=run.query,
        url=run.url,
        subject=run.subject,
        grade_band=run.grade_band,
        status=run.status,
        steps=loads(run.steps_json, []),
        inbox_count=run.inbox_count,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
    )


def list_inbox(session: Session, status: str | None = None, limit: int = 50) -> list[InboxItemOut]:
    q = session.query(CorpusInboxItem)
    if status:
        q = q.filter(CorpusInboxItem.status == status)
    rows = q.order_by(CorpusInboxItem.created_at.desc()).limit(limit).all()
    return [_inbox_out(r) for r in rows]


def _inbox_out(item: CorpusInboxItem) -> InboxItemOut:
    return InboxItemOut(
        id=item.id,
        scout_run_id=item.scout_run_id,
        status=item.status,
        source_url=item.source_url,
        source_platform=item.source_platform,
        resource_kind=item.resource_kind,
        title=item.title,
        snippet=item.snippet,
        proposed_node_ids=loads(item.proposed_node_ids_json, []),
        agent_confidence=item.agent_confidence,
        agent_notes=loads(item.agent_notes_json, {}),
        subject=item.subject,
        grade_band=item.grade_band,
        created_at=item.created_at,
    )


def search_nodes(
    session: Session,
    *,
    q: str = "",
    subject: str | None = None,
    node_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[CorpusNodeOut]:
    query = session.query(CorpusNode)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(
                CorpusNode.label.ilike(pattern),
                CorpusNode.summary.ilike(pattern),
                CorpusNode.id.ilike(pattern),
            )
        )
    if subject:
        query = query.filter(CorpusNode.subject == subject)
    if node_type:
        query = query.filter(CorpusNode.node_type == node_type)
    if status:
        query = query.filter(CorpusNode.status == status)
    rows = query.order_by(CorpusNode.updated_at.desc()).limit(limit).all()
    return [_node_out(session, r) for r in rows]


def _classify_rel(rel_type: str) -> str:
    if rel_type in LINK_GROUP_STRUCTURAL:
        return "structural"
    if rel_type in LINK_GROUP_KP:
        return "kp"
    if rel_type in LINK_GROUP_TAGS:
        return "tags"
    return "other"


def _parse_hide_set(hide: str | None) -> set[str]:
    if hide is None:
        return set(DEFAULT_HIDE_RELS)
    if hide.strip() == "":
        return set()
    return {h.strip() for h in hide.split(",") if h.strip()}


def get_node_links(
    session: Session,
    node_id: str,
    *,
    hide: str | None = None,
) -> NodeLinksOut:
    hide_set = _parse_hide_set(hide)
    out_edges = session.query(CorpusEdge).filter(CorpusEdge.from_id == node_id).all()
    in_edges = session.query(CorpusEdge).filter(CorpusEdge.to_id == node_id).all()
    outlinks: list[LinkEntryOut] = []
    inlinks: list[LinkEntryOut] = []
    structural: list[LinkEntryOut] = []
    kp: list[LinkEntryOut] = []
    tags: list[LinkEntryOut] = []
    other: list[LinkEntryOut] = []
    hidden_count = 0

    def _entry(e: CorpusEdge, nid: str, is_out: bool) -> LinkEntryOut:
        n = session.get(CorpusNode, nid)
        return LinkEntryOut(
            node_id=nid,
            label=n.label if n else nid,
            rel_type=e.rel_type,
            node_type=n.node_type if n else "",
            group=_classify_rel(e.rel_type),
        )

    for e in out_edges:
        if e.rel_type in hide_set:
            hidden_count += 1
            continue
        entry = _entry(e, e.to_id, True)
        outlinks.append(entry)
        bucket = {"structural": structural, "kp": kp, "tags": tags}.get(entry.group)
        if bucket is not None:
            bucket.append(entry)
        else:
            other.append(entry)
    for e in in_edges:
        if e.rel_type in hide_set:
            hidden_count += 1
            continue
        entry = _entry(e, e.from_id, False)
        inlinks.append(entry)

    return NodeLinksOut(
        outlinks=outlinks,
        inlinks=inlinks,
        structural=structural,
        kp=kp,
        tags=tags,
        other=other,
        hidden_count=hidden_count,
    )


def get_workqueue(
    session: Session,
    limit: int = 20,
    *,
    import_job_id: str | None = None,
) -> WorkQueueOut:
    draft_chunks = list_nodes(session, status="draft", node_type="chunk", limit=limit * 3)
    if import_job_id:
        draft_chunks = [
            n
            for n in draft_chunks
            if (n.meta or {}).get("import_job_id") == import_job_id
        ][:limit]
    else:
        draft_chunks = draft_chunks[:limit]
    draft_chunks.sort(
        key=lambda n: page_sort_key(
            node_id=n.id,
            label=n.label,
            source_page=(n.meta or {}).get("source_page"),
        )
    )
    review_nodes = list_nodes(session, status="review", limit=limit)
    unindexed: list[CorpusNodeOut] = []
    for n in (
        session.query(CorpusNode)
        .filter(CorpusNode.status == "published")
        .order_by(CorpusNode.updated_at.desc())
        .limit(500)
    ):
        if not loads(n.meta_json, {}).get("vector_indexed_at"):
            unindexed.append(_node_out(session, n))
            if len(unindexed) >= limit:
                break
    failed_rows = (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.status == "failed")
        .order_by(CorpusImportJob.updated_at.desc())
        .limit(limit)
        .all()
    )
    structure_review: list = []
    for j in (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.status == "chunked")
        .order_by(CorpusImportJob.updated_at.desc())
        .limit(80)
        .all()
    ):
        jm = loads(j.meta_json, {})
        if jm.get("needs_structure_review") or jm.get("structure_status") == "needs_structure_review":
            structure_review.append(j)
            if len(structure_review) >= limit:
                break
    inbox_rows = (
        session.query(CorpusInboxItem)
        .filter(CorpusInboxItem.status == "pending")
        .order_by(CorpusInboxItem.created_at.desc())
        .limit(limit)
        .all()
    )
    return WorkQueueOut(
        draft_chunks=draft_chunks,
        review_nodes=review_nodes,
        unindexed_published=unindexed,
        failed_imports=[_job_out(j) for j in failed_rows],
        structure_review_imports=[_job_out(j) for j in structure_review],
        inbox_ready=[_inbox_out(i) for i in inbox_rows],
    )


def start_reindex_async(session: Session, body: ReindexRequest) -> ReindexAsyncOut:
    from app.clients.redis_client import enqueue_reindex_job, set_reindex_status

    if body.force:
        raise ValueError("async reindex does not support force rebuild; use sync /reindex")
    if body.node_ids:
        node_ids = body.node_ids
    else:
        review = session.query(CorpusNode).filter(CorpusNode.status == "review").all()
        node_ids = [n.id for n in review]
    job_id = str(uuid.uuid4())
    set_reindex_status(
        job_id,
        {"job_id": job_id, "status": "queued", "done": 0, "total": len(node_ids), "errors": []},
    )
    enqueue_reindex_job(job_id, node_ids)
    return ReindexAsyncOut(job_id=job_id, status="queued")


def get_reindex_job_status(job_id: str) -> ReindexJobStatusOut | None:
    from app.clients.redis_client import get_reindex_status

    data = get_reindex_status(job_id)
    if not data:
        return None
    return ReindexJobStatusOut(**data)


def run_reindex_job(node_ids: list[str], job_id: str) -> None:
    from app.clients.redis_client import set_reindex_status

    total = len(node_ids)
    done = 0
    errors: list[str] = []
    set_reindex_status(
        job_id,
        {"job_id": job_id, "status": "running", "done": 0, "total": total, "errors": []},
    )
    factory = __import__("app.db.session", fromlist=["get_session_factory"]).get_session_factory()
    batch_size = 25
    for i in range(0, total, batch_size):
        batch = node_ids[i : i + batch_size]
        try:
            with factory() as session:
                publish_nodes_bulk(session, batch)
                session.commit()
            done += len(batch)
        except Exception as exc:
            logger.exception("reindex batch failed job=%s", job_id)
            errors.append(str(exc))
            done += len(batch)
        set_reindex_status(
            job_id,
            {
                "job_id": job_id,
                "status": "running",
                "done": done,
                "total": total,
                "errors": errors,
            },
        )
    set_reindex_status(
        job_id,
        {
            "job_id": job_id,
            "status": "completed" if not errors else "completed_with_errors",
            "done": done,
            "total": total,
            "errors": errors,
        },
    )


def bulk_publish(session: Session, body: BulkPublishRequest) -> dict:
    n = publish_nodes_bulk(session, body.node_ids)
    return {"published_count": n, "node_ids": body.node_ids}


def publish_subtree(session: Session, node_id: str) -> PublishSubtreeOut:
    ids = collect_subtree_ids(session, node_id)
    publishable = []
    for nid in ids:
        node = session.get(CorpusNode, nid)
        if node and node.status == "review":
            publishable.append(nid)
    root = session.get(CorpusNode, node_id)
    if root and root.status == "review" and node_id not in publishable:
        publishable.insert(0, node_id)
    count = publish_nodes_bulk(session, publishable)
    return PublishSubtreeOut(root_id=node_id, published_count=count, node_ids=publishable)


def set_node_status(session: Session, node_id: str, body: SetStatusRequest) -> CorpusNodeOut | None:
    node = session.get(CorpusNode, node_id)
    if node is None:
        return None
    try:
        apply_status_transition(session, node, body.status)
    except (StatusTransitionError, PublishGuardError) as e:
        raise ValueError(str(e)) from e
    session.flush()
    return _node_out(session, node)


def submit_node_for_review(session: Session, node_id: str) -> CorpusNodeOut | None:
    node = session.get(CorpusNode, node_id)
    if node is None:
        return None
    try:
        submit_for_review(session, node_id)
    except (StatusTransitionError, ValueError) as e:
        raise ValueError(str(e)) from e
    session.flush()
    return _node_out(session, node)


def reset_corpus_workspace_admin(session: Session, *, reseed: bool = False) -> dict:
    return reset_corpus_workspace(session, reseed=reseed)


def get_corpus_tree(
    session: Session,
    *,
    root: str,
    parent_id: str = "",
    subject: str | None = None,
    grade_band: str | None = None,
    depth: int = 1,
) -> list[TreeNodeOut]:
    return get_tree(
        session,
        root=root,
        parent_id=parent_id,
        subject=subject,
        grade_band=grade_band,
        depth=depth,
    )


def get_graph(
    session: Session,
    center: str,
    hops: int = 2,
    limit: int = 50,
    *,
    include_draft: bool = False,
    rel_types: str | None = None,
    root: str | None = None,
) -> GraphOut:
    rel_set: set[str] | None = None
    if rel_types:
        rel_set = {r.strip() for r in rel_types.split(",") if r.strip()}

    if root and root.startswith("textbook:"):
        tb_id = root.split(":", 1)[1]
        ids = collect_subtree_ids(session, tb_id)
        if center and center not in ids:
            ids = [center] + ids
    elif center:
        ids = [center]
    else:
        ids = [n.id for n in session.query(CorpusNode).filter(CorpusNode.status == "published").limit(10)]

    neighbors = expand_neighbors(
        session,
        ids,
        max_hops=hops,
        limit=limit,
        rel_types=rel_set,
        published_only=not include_draft,
    )
    node_map = {n.id: n for n in neighbors}
    for i in ids:
        n = session.get(CorpusNode, i)
        if n and (include_draft or n.status == "published"):
            node_map[n.id] = n
    all_ids = list(node_map.keys())
    edge_q = session.query(CorpusEdge).filter(
        CorpusEdge.from_id.in_(all_ids) | CorpusEdge.to_id.in_(all_ids)
    )
    if rel_set:
        edge_q = edge_q.filter(CorpusEdge.rel_type.in_(rel_set))
    edges = edge_q.limit(limit * 4).all()
    return GraphOut(
        nodes=[_node_out(session, node_map[nid]) for nid in all_ids if nid in node_map],
        edges=[
            {"id": e.id, "from_id": e.from_id, "to_id": e.to_id, "rel_type": e.rel_type}
            for e in edges
        ],
    )


def promote_from_question(
    session: Session,
    question_id: str,
    body: PromoteQuestionRequest,
) -> CorpusNodeOut:
    q = session.get(QuestionEntity, question_id)
    if q is None:
        raise ValueError("question not found")
    text = (q.print_text or "").strip()
    if not text:
        raise ValueError("question has no print_text")
    slug = re.sub(r"[^\w]+", "-", text[:24].lower())[:20] or uuid.uuid4().hex[:8]
    node_id = f"chunk:promoted:{q.subject}:{slug}"
    tags = loads(q.kp_tags_json, [])
    kp_id = tags[0].get("kp_id") if tags else ""
    row = upsert_draft_node(
        session,
        node_id=node_id,
        node_type=body.target_node_type,
        subject=q.subject,
        grade_band=q.grade_band,
        label=text[:80],
        summary=text[:240],
        full_text=text,
        tags=[f"grade_band:{q.grade_band}", "promoted:companion"],
        links={"primary_kp": kp_id} if kp_id else {},
        meta={
            "doc_type": body.target_node_type,
            "kp_id": kp_id,
            "source_question_id": question_id if not body.anonymize else "",
            "license": "user",
        },
        status="review",
        source_channel="companion",
    )
    return _node_out(session, row)
