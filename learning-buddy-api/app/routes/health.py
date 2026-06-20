from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.config import settings
from app.clients.nemo_retriever import nemo_embed_health, nemo_rerank_health
from app.clients.redis_client import redis_ok
from app.db.session import check_db_ok, db_session
from app.rag.corpus.pipeline import corpus_stats, full_rebuild
from app.rag.vector.factory import get_vector_store
from app.schemas.common import HealthResponse

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    s_count, q_count, _corpus_ok = corpus_stats()
    store = get_vector_store()
    vector_ok = store.health_ok()
    backend = (settings.vector_backend or "milvus").lower()
    milvus_ok = vector_ok if backend == "milvus" else False
    qdrant_ok = vector_ok if backend == "qdrant" else False
    return HealthResponse(
        db_ok=check_db_ok(),
        milvus_ok=milvus_ok,
        qdrant_ok=qdrant_ok,
        nemo_embed_ok=nemo_embed_health(),
        nemo_rerank_ok=nemo_rerank_health(),
        redis_ok=redis_ok(),
        chroma_ok=vector_ok,
        vector_backend=backend,
        syllabus_count=s_count,
        question_bank_count=q_count,
        embed_model=settings.nemo_embed_model if settings.nemo_embed_enabled else settings.embed_model,
        max_students=settings.max_students,
    )


@router.post("/reindex")
def reindex(force: bool = True, session: Session = Depends(db_session)):
    del session
    s, q = full_rebuild(force=force)
    return {"syllabus_count": s, "question_bank_count": q}
