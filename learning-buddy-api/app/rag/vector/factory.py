from __future__ import annotations

from app.config import settings
from app.rag.vector.base import VectorStore
from app.rag.vector.milvus_store import MilvusVectorStore
from app.rag.vector.qdrant_store import QdrantVectorStore


def get_vector_store() -> VectorStore:
    backend = (settings.vector_backend or "milvus").lower()
    if backend == "qdrant":
        return QdrantVectorStore()
    return MilvusVectorStore()
