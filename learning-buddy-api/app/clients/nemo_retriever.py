from __future__ import annotations

import logging
from functools import lru_cache

import httpx
from sentence_transformers import CrossEncoder

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _cross_encoder() -> CrossEncoder | None:
    try:
        device = settings.embed_device
        if device == "cuda":
            import torch

            if not torch.cuda.is_available():
                device = "cpu"
        logger.info("Loading rerank fallback: %s on %s", settings.rerank_model, device)
        return CrossEncoder(settings.rerank_model, device=device)
    except Exception as e:
        logger.warning("CrossEncoder rerank fallback unavailable: %s", e)
        return None


def _cross_encoder_rerank(query: str, documents: list[str], top_k: int) -> list[tuple[int, float]] | None:
    model = _cross_encoder()
    if model is None or not documents:
        return None
    pairs = [[query, d] for d in documents]
    scores = model.predict(pairs, show_progress_bar=False)
    ranked = sorted(enumerate(float(s) for s in scores), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


def nemo_embed(texts: list[str]) -> list[list[float]] | None:
    if not settings.nemo_embed_enabled or not texts:
        return None
    url = f"{settings.nemo_embed_url.rstrip('/')}/embeddings"
    try:
        with httpx.Client(timeout=120.0) as client:
            out: list[list[float]] = []
            for text in texts:
                r = client.post(
                    url,
                    json={"input": text, "model": settings.nemo_embed_model, "encoding_format": "float"},
                )
                r.raise_for_status()
                data = r.json()
                emb = data["data"][0]["embedding"]
                out.append(emb)
            return out
    except Exception as e:
        logger.warning("NeMo embed NIM failed: %s", e)
        return None


def nemo_rerank(query: str, documents: list[str], top_k: int | None = None) -> list[tuple[int, float]] | None:
    """Return list of (original_index, score) sorted by relevance."""
    if not documents:
        return None
    k = top_k or settings.rag_rerank_top_k
    if settings.nemo_rerank_enabled:
        url = f"{settings.nemo_rerank_url.rstrip('/')}/ranking"
        try:
            with httpx.Client(timeout=120.0) as client:
                r = client.post(
                    url,
                    json={
                        "model": settings.nemo_rerank_model,
                        "query": {"text": query},
                        "passages": [{"text": d} for d in documents],
                        "top_n": min(k, len(documents)),
                    },
                )
                r.raise_for_status()
                data = r.json()
                ranked: list[tuple[int, float]] = []
                for item in data.get("rankings", data.get("results", [])):
                    idx = int(item.get("index", item.get("corpus_id", 0)))
                    score = float(item.get("logit", item.get("score", 0.0)))
                    ranked.append((idx, score))
                if not ranked and "data" in data:
                    for item in data["data"]:
                        idx = int(item.get("index", 0))
                        score = float(item.get("relevance_score", item.get("score", 0.0)))
                        ranked.append((idx, score))
                if ranked:
                    return ranked[:k]
        except Exception as e:
            logger.warning("NeMo rerank NIM failed: %s", e)
    return _cross_encoder_rerank(query, documents, k)


def nemo_embed_health() -> bool:
    if not settings.nemo_embed_enabled:
        return False
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{settings.nemo_embed_url.rsplit('/v1', 1)[0]}/v1/models")
            return r.status_code == 200
    except Exception:
        return False


def nemo_rerank_health() -> bool:
    if not settings.nemo_rerank_enabled:
        return False
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.get(f"{settings.nemo_rerank_url.rsplit('/v1', 1)[0]}/v1/models")
            return r.status_code == 200
    except Exception:
        return False
