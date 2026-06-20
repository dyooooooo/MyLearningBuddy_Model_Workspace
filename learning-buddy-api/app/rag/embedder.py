from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.clients.nemo_retriever import nemo_embed
from app.config import settings

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="embed")


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    device = settings.embed_device
    if device == "cuda":
        import torch

        if not torch.cuda.is_available():
            device = "cpu"
            logger.warning("embed_device=cuda but CUDA unavailable; using CPU")
    logger.info("Loading embedding model: %s on %s", settings.embed_model, device)
    return SentenceTransformer(settings.embed_model, device=device)


def _encode_sync_cpu(texts: list[str]) -> list[list[float]]:
    model = get_embedder()
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if settings.nemo_embed_enabled:
        vecs = nemo_embed(texts)
        if vecs and len(vecs) == len(texts):
            return vecs
        logger.info("NeMo embed unavailable; falling back to %s %s", settings.embed_device, settings.embed_model)
    return _encode_sync_cpu(texts)


async def embed_texts_async(texts: list[str]) -> list[list[float]]:
    import asyncio

    if not texts:
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, embed_texts, texts)
