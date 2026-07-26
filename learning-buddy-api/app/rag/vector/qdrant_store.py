from __future__ import annotations

import logging
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.config import settings
from app.rag.corpus.schema import CorpusDocument
from app.rag.vector.base import VectorHit, VectorStore

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, timeout=30)


class QdrantVectorStore(VectorStore):
    def __init__(self) -> None:
        self.collection = settings.qdrant_collection
        self._client = _client()

    def recreate_collection(self) -> None:
        name = self.collection
        if self._client.collection_exists(name):
            self._client.delete_collection(name)
        self._client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=self._vector_size(), distance=qm.Distance.COSINE),
        )

    def _vector_size(self) -> int:
        from app.rag.embedder import embed_texts

        return len(embed_texts(["ping"])[0])

    def _ensure_collection(self, dim: int) -> None:
        if not self._client.collection_exists(self.collection):
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )

    def upsert_documents(
        self,
        docs: list[CorpusDocument],
        vectors: list[list[float]],
        *,
        status: str = "published",
        source_channel: str = "manual",
    ) -> None:
        if not docs:
            return
        self._ensure_collection(len(vectors[0]))
        points = []
        for doc, vec in zip(docs, vectors, strict=True):
            points.append(
                qm.PointStruct(
                    id=abs(hash(doc.doc_id)) % (2**63 - 1),
                    vector=vec,
                    payload={
                        "doc_id": doc.doc_id,
                        "node_id": doc.doc_id,
                        "doc_type": doc.doc_type,
                        "subject": doc.subject,
                        "grade_band": doc.grade_band,
                        "kp_id": doc.kp_id,
                        "label": doc.label,
                        "parent_id": doc.parent_id,
                        "text": doc.text,
                        "source": doc.doc_type,
                        "status": status,
                        "source_channel": source_channel,
                    },
                )
            )
        self._client.upsert(collection_name=self.collection, points=points, wait=True)

    def search(
        self,
        query_vector: list[float],
        *,
        subject: str,
        grade_band: str,
        top_k: int = 5,
        status_filter: str | None = "published",
    ) -> list[VectorHit]:
        if not self._client.collection_exists(self.collection):
            return []
        filt = qm.Filter(
            must=[
                qm.FieldCondition(key="subject", match=qm.MatchValue(value=subject)),
                qm.FieldCondition(key="grade_band", match=qm.MatchValue(value=grade_band)),
            ]
        )
        result = self._client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=filt,
            limit=top_k,
            with_payload=True,
        )
        hits: list[VectorHit] = []
        for p in result.points:
            pl = p.payload or {}
            hits.append(
                VectorHit(
                    doc_id=str(pl.get("doc_id", "")),
                    kp_id=str(pl.get("kp_id", "")),
                    score=float(p.score),
                    text=str(pl.get("text", "")),
                    label=str(pl.get("label", "")),
                    doc_type=str(pl.get("doc_type", "")),
                    source=str(pl.get("source", pl.get("doc_type", ""))),
                )
            )
        return hits

    def count(self) -> int:
        if not self._client.collection_exists(self.collection):
            return 0
        return self._client.count(collection_name=self.collection, exact=True).count

    def health_ok(self) -> bool:
        try:
            self._client.get_collections()
            return True
        except Exception as e:
            logger.warning("Qdrant health failed: %s", e)
            return False
