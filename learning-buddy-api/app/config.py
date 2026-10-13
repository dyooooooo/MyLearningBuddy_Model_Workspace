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
    rag_hybrid_alpha: float = 0.7  # legacy; main fusion is RRF
    rag_rrf_k: int = 60
    rag_hybrid_rrf_enabled: bool = True
    # Soft signal only (PDF textbook import deepen); never hard-split by chars.
    textbook_toc_deepen_soft_chars: int = 1200
    textbook_toc_deepen_enabled: bool = True
    kp_score_threshold: float = 0.55
    eval_confidence_threshold: float = 0.55

    graph_enabled: bool = True
    eval_agent_enabled: bool = True
    eval_fallback_rules: bool = True
    eval_use_bank_answer: bool = False
    eval_solve_thinking: bool = True
    eval_two_phase_solve: bool = True
    eval_hints_max_per_section: int = 2
    eval_debug_trace: bool = True
    eval_debug_log: bool = True
    eval_log_to_file: bool = True
    eval_log_dir: str = ""  # empty → buddy_data_dir/logs/eval

    vllm_base_url: str = "http://vllm-qwen36-35b:8088/v1"
    vllm_model: str = "qwen38-27b-nvfp4"
    vllm_max_concurrent: int = 2
    vllm_temperature: float = 0.0
    vllm_acquire_timeout_s: float = 120.0
    vllm_use_json_schema: bool = True

    max_students: int = 5
    admin_token: str = "change-me-admin"
    api_token: str = "change-me-api"

    corpus_dir: str = "/app/data"
    graph_queue: str = "lb:jobs:graph"
    edge_queue: str = "lb:jobs:edges"
    import_queue: str = "lb:jobs:import"
    # 35B layout line — fully isolated queue (never shares with the MinerU
    # import_queue). Worker routes it to app.import_35b.service.run_convert.
    import35b_queue: str = "lb:jobs:import35b"
    # When true, every 35B import page dumps a full debug bundle to MinIO under
    # ``{prefix}/35b/debug/`` (raw model output, blueprint, page image, HTML,
    # per-stage metrics).  This is the observability layer that lets us diff runs
    # and localize regressions per stage instead of blind-changing the pipeline.
    import35b_debug_dump: bool = True
    # Optional host mirror of per-page debug bundles (``.pageaudit/``). When set,
    # deleting a 35B job also removes matching files under this directory.
    import35b_pageaudit_dir: str = "/data/buddy/pageaudit"
    import35b_cursor_pack_dir: str = "/data/buddy/cursor-packs"
    # MinerU/corpus Cursor Repair Packs — under same host mount as 35B packs,
    # but in a ``corpus/`` subdirectory (isolation by path, not a new volume).
    corpus_cursor_pack_dir: str = "/data/buddy/cursor-packs/corpus"
    # When true, dump per-page debug under corpus/imports/.../mineru/debug/
    corpus_debug_dump: bool = True
    scout_queue: str = "lb:jobs:scout"
    publish_queue: str = "lb:jobs:publish"
    # Knowledge-base MinerU import — isolated from corpus import_queue.
    kb_import_queue: str = "lb:jobs:kb_import"
    textbook_eval_queue: str = "lb:jobs:textbook_eval"

    textbook_agent_url: str = ""
    textbook_agent_token: str = ""
    textbook_agent_timeout_s: float = 300.0
    agent_textbooks_dir: str = "/data/agent/textbooks"

    milvus_staging_collection: str = "lb_corpus_staging"

    # ── 35B v4 判例治理（预留能力，默认全关；激活门槛见 caselaw_min_cases）──
    # 判例向量库复用共享 RAG 栈（embedder + Milvus 连接），但落在**独立
    # collection**，与语料库 lb_corpus 物理隔离；canonical 行落 35B 自有
    # Postgres 表 import35b_caselaw。开关默认 False —— 架构文档 4.5 规定
    # 「已确认案例 > 100 条」才激活，未激活时全部降级为 no-op，不改现有行为。
    import35b_caselaw_enabled: bool = False
    import35b_caselaw_collection: str = "import35b_caselaw"
    # 判例库激活门槛：Postgres 判例行数 < 此值时 enrich 直接跳过（仍持续记录）。
    import35b_caselaw_min_cases: int = 100
    # 误判库命中降权阈值：相似度 ≥ 此值才把 finding 降一档 + 加「疑似历史误报」
    # 候选（永不静默抑制，抑制只能由人经 suppression 表显式完成）。
    import35b_caselaw_fp_threshold: float = 0.90
    # 方案库候选建议阈值：相似度 ≥ 此值的历史修正方案作为候选诊断补入。
    import35b_caselaw_hint_threshold: float = 0.82
    import35b_caselaw_top_k: int = 5
    # T2 Agent 视觉比对（异步、仅产疑点 detector=agent，永不改数据）。默认关闭：
    # 依赖 35B VLM，激活条件同判例库；关闭时 run_agent_checks 返回 []。
    import35b_agent_check_enabled: bool = False
    import35b_agent_check_max_tokens: int = 1536

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "companion-frames"
    minio_secure: bool = False

    # Monetization tiers
    trial_days: int = 14
    tier_free_features: str = "ocr,archive,kp_basic"
    tier_paid_features: str = "eval,aplus,fluency,diagnosis,planning,explain,recommend,voice"
    tier_b2b_features: str = "corpus_admin,seat,whitelabel,class_dashboard"

    nv_ingest_url: str = "http://nv-ingest:7670"

    # Optional external Math-Formula-Recognition side model (Pix2Text /
    # UniMERNet).  Disabled by default → the 35B blueprint LaTeX is used as-is
    # (skeleton unchanged).  When enabled, equation blocks are re-cropped and
    # sent to the MFR service; on any failure the pipeline falls back silently.
    mfr_enabled: bool = False
    mfr_url: str = ""                     # e.g. http://pix2text:8503/mfr
    mfr_backend: str = "http"             # http | pix2text | unimernet
    mfr_timeout_s: float = 20.0
    mfr_dpi: int = 200
    mfr_min_latex_len: int = 2

    # MinerU cloud API (https://mineru.net)
    # Leave mineru_api_token empty to disable MinerU and use Vision OCR instead.
    mineru_api_token: str = ""
    mineru_api_base_url: str = "https://mineru.net"
    mineru_language: str = "ch"           # "ch" for Chinese; "" to omit
    mineru_enable_formula: bool = True
    mineru_enable_table: bool = True
    mineru_is_ocr: bool = False           # set True only for image-only PDFs
    mineru_model_version: str = "vlm"
    mineru_timeout_s: float = 600.0       # large textbooks may need >300 s

    @property
    def db_path(self) -> Path:
        return Path(self.buddy_data_dir) / "buddy.db"

    @property
    def manifest_path(self) -> Path:
        return Path(self.buddy_data_dir) / "corpus_manifest.json"

    @property
    def use_postgres(self) -> bool:
        return bool(self.database_url.strip())

    @property
    def eval_log_path(self) -> Path:
        if self.eval_log_dir.strip():
            return Path(self.eval_log_dir)
        return Path(self.buddy_data_dir) / "logs" / "eval"


settings = Settings()
