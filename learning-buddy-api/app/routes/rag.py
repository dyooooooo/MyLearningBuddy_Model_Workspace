from fastapi import APIRouter, Query

from app.rag.llamaindex_retrieval import property_graph_retrieve
from app.rag.retriever import search
from app.schemas.common import RagSearchHit

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


@router.get("/search", response_model=list[RagSearchHit])
def rag_search(
    q: str,
    subject: str = Query(default="math"),
    grade_band: str = Query(default="hs"),
    top_k: int | None = None,
) -> list[RagSearchHit]:
    return search(q, subject=subject, grade_band=grade_band, top_k=top_k)


@router.get("/eval-context")
def rag_eval_context(
    q: str,
    subject: str = Query(default="math"),
    grade_band: str = Query(default="hs"),
) -> dict:
    return property_graph_retrieve(q, subject=subject, grade_band=grade_band)
