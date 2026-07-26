from __future__ import annotations

from app.config import settings
from app.rag.vector.base import VectorStore
from app.rag.vector.milvus_store import MilvusVectorStore
from app.rag.vector.qdrant_store import QdrantVectorStore


def get_vector_store(collection: str | None = None) -> VectorStore:
    backend = (settings.vector_backend or "milvus").lower()
    if backend == "qdrant":
        return QdrantVectorStore()
    name = collection or settings.milvus_collection
    return MilvusVectorStore(collection_name=name)


def get_staging_vector_store() -> VectorStore:
    backend = (settings.vector_backend or "milvus").lower()
    if backend == "qdrant":
        return QdrantVectorStore()
    return MilvusVectorStore(collection_name=settings.milvus_staging_collection)
