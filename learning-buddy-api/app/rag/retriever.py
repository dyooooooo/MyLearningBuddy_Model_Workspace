from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.clients.nemo_retriever import nemo_rerank
from app.config import settings
from app.db.session import get_session_factory
from app.rag.corpus.pipeline import kp_label
from app.rag.embedder import embed_texts
from app.rag.keyword_score import hybrid_score, keyword_score_for_kp
from app.rag.vector.factory import get_vector_store
from app.schemas.common import RagSearchHit
from app.services.corpus_graph import expand_neighbors
from app.util.json_util import loads

logger = logging.getLogger(__name__)


@dataclass
class EvalContext:
    query: str
    subject: str
    grade_band: str
    entry_node_ids: list[str] = field(default_factory=list)
    hits: list[RagSearchHit] = field(default_factory=list)
    kps: list[dict[str, Any]] = field(default_factory=list)
    methods: list[dict[str, Any]] = field(default_factory=list)
    shortcuts: list[dict[str, Any]] = field(default_factory=list)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_node_ids": self.entry_node_ids,
            "hits": [h.model_dump() for h in self.hits],
            "kps": self.kps,
            "methods": self.methods,
            "shortcuts": self.shortcuts,
            "chunks": self.chunks,
            "tags": self.tags,
        }


def search(
    query: str,
    *,
    subject: str = "math",
    grade_band: str = "hs",
    top_k: int | None = None,
) -> list[RagSearchHit]:
    k = top_k or settings.rag_top_k
    if not query.strip():
        return []

    query_emb = embed_texts([query])[0]
    store = get_vector_store()
    raw = store.search(query_emb, subject=subject, grade_band=grade_band, top_k=k)

    hits: list[RagSearchHit] = []
    for h in raw:
        kw = keyword_score_for_kp(query, h.kp_id, h.text, subject=subject, grade_band=grade_band)
        score = hybrid_score(h.score, kw)
        hits.append(
            RagSearchHit(
                kp_id=h.kp_id,
                score=round(score, 4),
                source=h.source,
                text=h.text,
                label=h.label or kp_label(h.kp_id),
            )
        )

    hits.sort(key=lambda x: x.score, reverse=True)
    return hits[: k]


def _rerank_hits(query: str, hits: list[RagSearchHit]) -> list[RagSearchHit]:
    if len(hits) <= settings.rag_rerank_top_k:
        return hits
    docs = [h.text for h in hits]
    ranked = nemo_rerank(query, docs, top_k=settings.rag_rerank_top_k)
    if not ranked:
        return hits[: settings.rag_rerank_top_k]
    out: list[RagSearchHit] = []
    for idx, score in ranked:
        if 0 <= idx < len(hits):
            h = hits[idx]
            out.append(
                RagSearchHit(
                    kp_id=h.kp_id,
                    score=round(score, 4),
                    source=h.source + "+rerank",
                    text=h.text,
                    label=h.label,
                )
            )
    return out or hits[: settings.rag_rerank_top_k]


def _node_to_bucket(node) -> dict[str, Any]:
    return {
        "id": node.id,
        "node_type": node.node_type,
        "label": node.label,
        "summary": node.summary,
        "tags": loads(node.tags_json, []),
        "meta": loads(node.meta_json, {}),
    }


def retrieve_eval_context(
    query: str,
    *,
    subject: str = "math",
    grade_band: str = "hs",
) -> EvalContext:
    """vector entry → rerank → graph expand → tag/grade filter."""
    hits = search(query, subject=subject, grade_band=grade_band, top_k=settings.rag_top_k)
    hits = _rerank_hits(query, hits)
    entry_ids = []
    factory = get_session_factory()
    ctx = EvalContext(query=query, subject=subject, grade_band=grade_band, hits=hits)

    with factory() as session:
        store = get_vector_store()
        raw = store.search(embed_texts([query])[0], subject=subject, grade_band=grade_band, top_k=settings.rag_rerank_top_k)
        for h in raw:
            if h.doc_id:
                entry_ids.append(h.doc_id)
        for h in hits:
            if h.kp_id and h.kp_id not in entry_ids:
                entry_ids.append(h.kp_id)
        ctx.entry_node_ids = entry_ids

        neighbors = expand_neighbors(session, entry_ids, max_hops=2, limit=40)
        tag_set: set[str] = set()
        for n in neighbors:
            bucket = _node_to_bucket(n)
            tag_set.update(bucket["tags"])
            if n.node_type == "kp":
                ctx.kps.append(bucket)
            elif n.node_type == "method":
                ctx.methods.append(bucket)
            elif n.node_type == "shortcut":
                ctx.shortcuts.append(bucket)
            elif n.node_type in ("chunk", "section", "textbook"):
                ctx.chunks.append(bucket)
        ctx.tags = sorted(tag_set)

    return ctx


def map_question_to_kp(
    print_text: str,
    *,
    subject: str = "math",
    grade_band: str = "hs",
) -> tuple[list[dict], list[dict]]:
    hits = search(print_text, subject=subject, grade_band=grade_band)
    by_kp: dict[str, list[RagSearchHit]] = {}
    for h in hits:
        by_kp.setdefault(h.kp_id, []).append(h)

    merged: list[tuple[str, float, str, str]] = []
    for kp_id, group in by_kp.items():
        score = sum(h.score for h in group) / len(group)
        sources = ",".join(sorted({h.source for h in group}))
        label = group[0].label or kp_label(kp_id)
        merged.append((kp_id, score, sources, label))

    merged.sort(key=lambda x: x[1], reverse=True)
    threshold = settings.kp_score_threshold

    kp_tags: list[dict] = []
    evidence: list[dict] = []
    for kp_id, score, sources, label in merged[:3]:
        evidence.append(
            {
                "kp_id": kp_id,
                "score": round(score, 4),
                "source": sources,
                "text": print_text[:200],
            }
        )
        if score >= threshold and not kp_tags:
            kp_tags.append(
                {
                    "kp_id": kp_id,
                    "score": round(score, 4),
                    "source": sources,
                    "label": label,
                }
            )
    return kp_tags, evidence
