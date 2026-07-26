from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import CorpusNode
from app.rag.corpus.schema import CorpusDocument
from app.rag.embedder import embed_texts
from app.rag.vector.factory import get_staging_vector_store, get_vector_store
from app.services.corpus_governance import PublishGuardError, assert_publishable
from app.services.corpus_locale import LocaleValidationError, assert_publish_locale, apply_corpus_locale
from app.services.corpus_graph import add_edge, link_tag, upsert_node
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def node_to_corpus_document(node: CorpusNode) -> CorpusDocument:
    meta = loads(node.meta_json, {})
    tags = loads(node.tags_json, [])
    links = meta.get("links") or {}
    kp_id = meta.get("kp_id") or links.get("primary_kp") or ""
    doc_type = meta.get("doc_type") or node.node_type
    text = node.full_text or node.summary or node.label
    summary = node.summary or text[:240]
    content_format = meta.get("content_format") or ""
    if content_format == "html" or (text.strip().startswith("<") and "lb-page" in text):
        from app.services.document_ir import html_to_plain_text

        plain = html_to_plain_text(text)
        embed_text = f"{node.label}\n{summary}\n{plain}".strip()[:8000]
    else:
        embed_text = f"{node.label}\n{summary}\n{text}".strip()[:8000]
    return CorpusDocument(
        doc_id=node.id,
        doc_type=doc_type,  # type: ignore[arg-type]
        subject=node.subject,
        grade_band=node.grade_band,
        kp_id=str(kp_id),
        text=text,
        label=node.label,
        summary=summary,
        full_text=node.full_text or text,
        node_type=node.node_type,  # type: ignore[arg-type]
        tags=tags,
        links=links,
        meta={
            **meta,
            "kp_id": kp_id,
            "doc_type": doc_type,
            "vector_status": node.status,
            "source_channel": meta.get("source_channel", "manual"),
            "content_pack_id": meta.get("content_pack_id", ""),
        },
    )


def _sync_node_links(session: Session, node: CorpusNode) -> None:
    meta = loads(node.meta_json, {})
    links = meta.get("links") or {}
    for tag in loads(node.tags_json, []):
        tid = tag if str(tag).startswith("tag:") else f"tag:{tag}"
        link_tag(session, node.id, tid, source="studio")
    primary_kp = links.get("primary_kp") or meta.get("kp_id")
    if primary_kp and node.node_type in ("chunk", "question", "media"):
        add_edge(session, from_id=node.id, to_id=str(primary_kp), rel_type="defines_kp", source="studio")
        if node.node_type == "question":
            add_edge(session, from_id=node.id, to_id=str(primary_kp), rel_type="covers_kp", source="studio")
    for kp in links.get("secondary_kp", links.get("covers_kp", [])):
        add_edge(session, from_id=node.id, to_id=str(kp), rel_type="covers_kp", source="studio")
    sec = links.get("section_id")
    if sec and node.node_type == "chunk":
        add_edge(session, from_id=str(sec), to_id=node.id, rel_type="contains_chunk", source="studio")
    tb = links.get("textbook_id")
    parent_sec = links.get("parent_section_id")
    if parent_sec and node.node_type == "section":
        add_edge(session, from_id=str(parent_sec), to_id=node.id, rel_type="contains_section", source="studio")
    elif tb and node.node_type == "section":
        add_edge(session, from_id=str(tb), to_id=node.id, rel_type="contains_section", source="studio")


def embed_and_upsert_nodes(
    session: Session,
    node_ids: list[str],
    *,
    staging: bool = False,
) -> int:
    if not node_ids:
        return 0
    docs: list[CorpusDocument] = []
    for nid in node_ids:
        node = session.get(CorpusNode, nid)
        if node is None:
            continue
        docs.append(node_to_corpus_document(node))
    if not docs:
        return 0
    vectors = embed_texts([d.embed_text for d in docs])
    store = get_staging_vector_store() if staging else get_vector_store()
    status = "draft" if staging else "published"
    channel = docs[0].meta.get("source_channel", "manual")
    store.upsert_documents(docs, vectors, status=status, source_channel=str(channel))
    for nid in node_ids:
        node = session.get(CorpusNode, nid)
        if node:
            meta = loads(node.meta_json, {})
            meta["vector_indexed_at"] = _now_iso()
            meta["vector_collection"] = "staging" if staging else "production"
            node.meta_json = dumps(meta)
    session.flush()
    return len(docs)


def publish_node(session: Session, node_id: str) -> int:
    node = session.get(CorpusNode, node_id)
    if node is None:
        raise ValueError(f"node not found: {node_id}")
    assert_publishable(node)
    assert_publish_locale(
        node.subject,
        label=node.label,
        summary=node.summary,
        full_text=node.full_text,
    )
    node.status = "published"
    node.updated_at = _now_iso()
    _sync_node_links(session, node)
    count = embed_and_upsert_nodes(session, [node_id], staging=False)
    meta = loads(node.meta_json, {})
    meta["published_at"] = _now_iso()
    node.meta_json = dumps(meta)
    session.flush()
    return count


def publish_nodes_bulk(session: Session, node_ids: list[str]) -> int:
    total = 0
    for nid in node_ids:
        try:
            total += publish_node(session, nid)
        except (ValueError, PublishGuardError, LocaleValidationError) as exc:
            logger.warning("skip publish node %s: %s", nid, exc)
    return total


def embed_staging(session: Session, node_id: str) -> int:
    node = session.get(CorpusNode, node_id)
    if node is None:
        raise ValueError(f"node not found: {node_id}")
    _sync_node_links(session, node)
    return embed_and_upsert_nodes(session, [node_id], staging=True)


def upsert_draft_node(
    session: Session,
    *,
    node_id: str,
    node_type: str,
    subject: str,
    grade_band: str,
    label: str = "",
    summary: str = "",
    full_text: str = "",
    tags: list[str] | None = None,
    links: dict | None = None,
    meta: dict | None = None,
    status: str = "draft",
    source_channel: str = "manual",
) -> CorpusNode:
    merged_meta = {**(meta or {}), "links": links or {}, "source_channel": source_channel}
    if links and links.get("primary_kp"):
        merged_meta["kp_id"] = links["primary_kp"]
    label, summary, full_text, merged_meta, locale_warnings = apply_corpus_locale(
        subject,
        label=label or node_id,
        summary=summary,
        full_text=full_text,
        meta=merged_meta,
    )
    if locale_warnings:
        logger.info("locale warnings node=%s: %s", node_id, locale_warnings[:2])
    row = upsert_node(
        session,
        node_id=node_id,
        node_type=node_type,
        subject=subject,
        grade_band=grade_band,
        label=label or node_id,
        summary=summary,
        full_text=full_text,
        tags_json=tags or [f"grade_band:{grade_band}", f"subject:{subject}"],
        meta_json=merged_meta,
        status=status,
    )
    _sync_node_links(session, row)
    return row
