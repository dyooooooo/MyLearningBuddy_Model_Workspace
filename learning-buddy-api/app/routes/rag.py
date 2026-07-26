import json
from pathlib import Path

from fastapi import APIRouter, Query, Request

from app.config import settings
from app.rag.kp_resolver import resolve_kp_label_pair
from app.rag.llamaindex_retrieval import property_graph_retrieve
from app.rag.retriever import search
from app.schemas.common import RagSearchHit, SyllabusKpOut
from app.util.locale import locale_from_request

router = APIRouter(prefix="/api/v1/rag", tags=["rag"])


@router.get("/search", response_model=list[RagSearchHit])
def rag_search(
    request: Request,
    q: str,
    subject: str = Query(default="math"),
    grade_band: str = Query(default="hs"),
    top_k: int | None = None,
) -> list[RagSearchHit]:
    locale = locale_from_request(request)
    hits = search(q, subject=subject, grade_band=grade_band, top_k=top_k)
    out: list[RagSearchHit] = []
    for h in hits:
        pair = resolve_kp_label_pair(h.kp_id)
        label = pair.en if locale == "en" else pair.zh
        out.append(
            RagSearchHit(
                kp_id=h.kp_id,
                score=h.score,
                source=h.source,
                text=h.text,
                label=label,
            )
        )
    return out


@router.get("/eval-context")
def rag_eval_context(
    q: str,
    subject: str = Query(default="math"),
    grade_band: str = Query(default="hs"),
) -> dict:
    return property_graph_retrieve(q, subject=subject, grade_band=grade_band)


@router.get("/syllabus-kps", response_model=list[SyllabusKpOut])
def syllabus_kps(
    request: Request,
    subject: str = Query(default="math"),
    grade_band: str = Query(default="hs"),
) -> list[SyllabusKpOut]:
    locale = locale_from_request(request)
    path = Path(settings.corpus_dir) / "syllabus" / f"{subject}_{grade_band}.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])
    out: list[SyllabusKpOut] = []
    for n in nodes:
        kp_id = n.get("id", "")
        if not kp_id:
            continue
        pair = resolve_kp_label_pair(kp_id)
        label = pair.en if locale == "en" else pair.zh
        out.append(
            SyllabusKpOut(
                kp_id=kp_id,
                label=label,
                label_i18n=pair,
                description=n.get("description_en" if locale == "en" else "description", n.get("description", "")),
                level=int(n.get("level", 1)),
                parent_id=n.get("parent_id"),
            )
        )
    return out
