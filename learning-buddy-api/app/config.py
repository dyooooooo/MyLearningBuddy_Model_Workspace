from __future__ import annotations

import logging
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8100
    buddy_data_dir: str = "/data/buddy"
    database_url: str = ""
    redis_url: str = "redis://redis:6379/0"

    # Vector store: milvus (primary) or qdrant (fallback)
    vector_backend: str = "milvus"
    milvus_uri: str = "http://milvus:19530"
    milvus_collection: str = "lb_corpus"
    milvus_enable_gpu_search: bool = False
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "lb_corpus"

    # NeMo Retriever NIM (OpenAI-compatible)
    nemo_embed_url: str = "http://nemo-embed:8000/v1"
    nemo_embed_model: str = "nvidia/llama-3.2-nv-embedqa-1b-v2"
    nemo_rerank_url: str = "http://nemo-rerank:8000/v1"
    nemo_rerank_model: str = "nvidia/llama-3.2-nv-rerankqa-1b-v2"
    nemo_embed_enabled: bool = True
    nemo_rerank_enabled: bool = True
    embed_dim: int = 2048

    embed_model: str = "BAAI/bge-small-zh-v1.5"
    embed_device: str = "cuda"  # cuda | cpu — used when NeMo NIM unavailable
    rerank_model: str = "BAAI/bge-reranker-v2-m3"  # GPU fallback when NeMo rerank NIM unavailable
    rag_top_k: int = 30
    rag_rerank_top_k: int = 5
    rag_hybrid_alpha: float = 0.7
    kp_score_threshold: float = 0.55
    eval_confidence_threshold: float = 0.55

    graph_enabled: bool = True
    eval_agent_enabled: bool = True
    eval_fallback_rules: bool = True

    vllm_base_url: str = "http://vllm-qwen36-35b:8088/v1"
    vllm_model: str = "qwen36-35b-nvfp4"
    vllm_max_concurrent: int = 2
    vllm_temperature: float = 0.0
    vllm_acquire_timeout_s: float = 120.0

    max_students: int = 5
    admin_token: str = "change-me-admin"
    api_token: str = "change-me-api"

    corpus_dir: str = "/app/data"
    graph_queue: str = "lb:jobs:graph"
    edge_queue: str = "lb:jobs:edges"

    # Monetization tiers
    trial_days: int = 14
    tier_free_features: str = "ocr,archive,kp_basic"
    tier_paid_features: str = "eval,aplus,fluency,diagnosis,planning,explain,recommend,voice"
    tier_b2b_features: str = "corpus_admin,seat,whitelabel,class_dashboard"

    nv_ingest_url: str = "http://nv-ingest:7670"

    @property
    def db_path(self) -> Path:
        return Path(self.buddy_data_dir) / "buddy.db"

    @property
    def manifest_path(self) -> Path:
        return Path(self.buddy_data_dir) / "corpus_manifest.json"

    @property
    def use_postgres(self) -> bool:
        return bool(self.database_url.strip())


settings = Settings()
