from __future__ import annotations

import logging
from functools import lru_cache

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

from app.config import settings
from app.rag.corpus.schema import CorpusDocument
from app.rag.vector.base import VectorHit, VectorStore

logger = logging.getLogger(__name__)

DENSE_FIELD = "dense"

_SCHEMA_FIELDS = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="node_id", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="doc_type", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="node_type", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="subject", dtype=DataType.VARCHAR, max_length=32),
    FieldSchema(name="grade_band", dtype=DataType.VARCHAR, max_length=16),
    FieldSchema(name="kp_id", dtype=DataType.VARCHAR, max_length=256),
    FieldSchema(name="label", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=8192),
    FieldSchema(name="question_type", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="difficulty", dtype=DataType.VARCHAR, max_length=32),
    FieldSchema(name="status", dtype=DataType.VARCHAR, max_length=32),
    FieldSchema(name="source_channel", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="content_pack_id", dtype=DataType.VARCHAR, max_length=128),
    FieldSchema(name=DENSE_FIELD, dtype=DataType.FLOAT_VECTOR, dim=0),  # dim set at runtime
]


@lru_cache(maxsize=1)
def _connected() -> bool:
    try:
        connections.connect(alias="default", uri=settings.milvus_uri)
        return True
    except Exception as e:
        logger.warning("Milvus connect failed: %s", e)
        return False


_EXPECTED_FIELD_NAMES = {
    "id", "node_id", "doc_type", "node_type", "subject", "grade_band",
    "kp_id", "label", "text", "question_type", "difficulty", "status",
    "source_channel", "content_pack_id", DENSE_FIELD,
}


class MilvusVectorStore(VectorStore):
    """Dense vector store. Hybrid = dense search here + lexical RRF in retriever.search."""

    def __init__(self, collection_name: str | None = None) -> None:
        self.collection_name = collection_name or settings.milvus_collection
        _connected()

    def _dim(self) -> int:
        from app.rag.embedder import embed_texts

        return len(embed_texts(["ping"])[0])

    def schema_ok(self) -> bool:
        """Return True if collection schema matches current code."""
        if not utility.has_collection(self.collection_name):
            return True
        try:
            col = Collection(self.collection_name)
            existing = {f.name for f in col.schema.fields}
            return _EXPECTED_FIELD_NAMES.issubset(existing)
        except Exception as e:
            logger.warning("Milvus schema check failed: %s", e)
            return False

    def _ensure_collection(self, dim: int) -> Collection:
        fields = list(_SCHEMA_FIELDS)
        fields[-1] = FieldSchema(name=DENSE_FIELD, dtype=DataType.FLOAT_VECTOR, dim=dim)
        if not utility.has_collection(self.collection_name):
            schema = CollectionSchema(fields, description="Learning Buddy corpus")
            col = Collection(self.collection_name, schema)
            index_params = {
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {"M": 16, "efConstruction": 200},
            }
            col.create_index(field_name=DENSE_FIELD, index_params=index_params)
            return col
        col = Collection(self.collection_name)
        existing = {f.name for f in col.schema.fields}
        if not _EXPECTED_FIELD_NAMES.issubset(existing):
            logger.warning(
                "Milvus collection %s schema mismatch (have %s), recreating",
                self.collection_name,
                existing,
            )
            utility.drop_collection(self.collection_name)
            schema = CollectionSchema(fields, description="Learning Buddy corpus")
            col = Collection(self.collection_name, schema)
            index_params = {
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {"M": 16, "efConstruction": 200},
            }
            col.create_index(field_name=DENSE_FIELD, index_params=index_params)
            return col
        return col

    def recreate_collection(self) -> None:
        if utility.has_collection(self.collection_name):
            utility.drop_collection(self.collection_name)
        self._ensure_collection(self._dim())

    def _doc_rows(
        self,
        docs: list[CorpusDocument],
        vectors: list[list[float]],
        *,
        status: str = "published",
        source_channel: str = "manual",
    ) -> list:
        return [
            [d.node_id for d in docs],
            [d.doc_type for d in docs],
            [(d.node_type or d.doc_type or "")[:64] for d in docs],
            [d.subject for d in docs],
            [d.grade_band for d in docs],
            [d.kp_id for d in docs],
            [d.label for d in docs],
            [d.embed_text[:8000] for d in docs],
            [str(d.meta.get("question_type", d.tags[0] if d.tags else ""))[:64] for d in docs],
            [str(d.meta.get("difficulty", ""))[:32] for d in docs],
            [str(d.meta.get("vector_status", status))[:32] for d in docs],
            [str(d.meta.get("source_channel", source_channel))[:64] for d in docs],
            [str(d.meta.get("content_pack_id", ""))[:128] for d in docs],
            vectors,
        ]

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
        dim = len(vectors[0])
        col = self._ensure_collection(dim)
        col.load()
        node_ids = list({d.node_id for d in docs if d.node_id})
        if node_ids and col.num_entities > 0:
            quoted = ", ".join(f'"{nid.replace(chr(34), "")}"' for nid in node_ids)
            col.delete(f"node_id in [{quoted}]")
        for d in docs:
            d.meta.setdefault("vector_status", status)
            d.meta.setdefault("source_channel", source_channel)
        rows = self._doc_rows(docs, vectors, status=status, source_channel=source_channel)
        col.insert(rows)
        col.flush()
        col.load()

    def search(
        self,
        query_vector: list[float],
        *,
        subject: str,
        grade_band: str,
        top_k: int = 5,
        status_filter: str | None = "published",
    ) -> list[VectorHit]:
        if not utility.has_collection(self.collection_name):
            return []
        col = Collection(self.collection_name)
        col.load()
        expr_parts = [f'subject == "{subject}"', f'grade_band == "{grade_band}"']
        if status_filter:
            expr_parts.append(f'status == "{status_filter}"')
        expr = " && ".join(expr_parts)
        try:
            results = col.search(
                data=[query_vector],
                anns_field=DENSE_FIELD,
                param={"metric_type": "COSINE", "params": {"ef": 64}},
                limit=top_k,
                expr=expr,
                output_fields=[
                    "node_id",
                    "kp_id",
                    "text",
                    "label",
                    "doc_type",
                    "node_type",
                    "question_type",
                    "source_channel",
                ],
            )
        except Exception as e:
            logger.warning("Milvus search with status filter failed, retry without: %s", e)
            expr = f'subject == "{subject}" && grade_band == "{grade_band}"'
            results = col.search(
                data=[query_vector],
                anns_field=DENSE_FIELD,
                param={"metric_type": "COSINE", "params": {"ef": 64}},
                limit=top_k,
                expr=expr,
                output_fields=["node_id", "kp_id", "text", "label", "doc_type", "question_type"],
            )
        hits: list[VectorHit] = []
        for group in results:
            for p in group:
                entity = p.entity

                def _f(name: str) -> str:
                    val = entity.get(name) if hasattr(entity, "get") else getattr(entity, name, "")
                    return str(val or "")

                hits.append(
                    VectorHit(
                        doc_id=_f("node_id"),
                        kp_id=_f("kp_id"),
                        score=float(p.score),
                        text=_f("text"),
                        label=_f("label"),
                        doc_type=_f("doc_type"),
                        source=_f("source_channel") or _f("doc_type"),
                    )
                )
        return hits

    def count(self) -> int:
        if not utility.has_collection(self.collection_name):
            return 0
        col = Collection(self.collection_name)
        col.flush()
        return col.num_entities

    def health_ok(self) -> bool:
        try:
            return _connected() and (not utility.has_collection(self.collection_name) or True)
        except Exception as e:
            logger.warning("Milvus health failed: %s", e)
            return False

    def delete_by_node_ids(self, node_ids: list[str]) -> None:
        if not node_ids or not utility.has_collection(self.collection_name):
            return
        col = Collection(self.collection_name)
        col.load()
        quoted = ", ".join(f'"{nid.replace(chr(34), "")}"' for nid in node_ids)
        col.delete(f"node_id in [{quoted}]")
        col.flush()
