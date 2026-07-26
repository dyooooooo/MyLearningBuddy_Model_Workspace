# AGENTS.md — MyLearningBuddy Model Workspace

This guide is written for AI coding agents that need to work in the repository at `~/MyLearningBuddy_Model_Workspace/`. The workspace is a **mono-directory** collection of related projects rather than a single package; most subsystems have their own dependency files, entry points, and Docker Compose stacks.

> **Language note:** The codebase is mixed Chinese/English. Source identifiers, docstrings, and the DeepTutor documentation are mostly English. Operational docs (e.g. `doc/learning-buddy-status-and-roadmap.md`, `learning-buddy-c2/README.md`) and many inline comments for the Learning Buddy services are Chinese. Keep identifiers in English and preserve Chinese terms when they appear in existing paths or data schemas.

---

## 1. Project overview

The workspace builds an **AI learning companion** with several integrated layers:

- **DeepTutor** (`DeepTutor/`) — A general-purpose agent-native tutoring platform (RAG, multi-agent, Web UI, CLI, API). Forked from the HKUDS open-source project.
- **Learning Buddy API** (`learning-buddy-api/`) — A pilot FastAPI backend focused on Chinese K-12 education: OCR import, corpus management, homework evaluation, mastery tracking, and RAG over textbooks.
- **Exam OCR Server / C2 Gateway** (`exam-ocr-server/`) — FastAPI gateway that exposes the local vLLM vision endpoint, an OpenAI-compatible LAN proxy, and the companion/studio APIs used by the Android tablet.
- **Learning Buddy C2** (`learning-buddy-c2/`) — Docker Compose orchestration for the Spark dual-Docker stack: vLLM Qwen3.6‑35B‑NVFP4, PostgreSQL, Redis, Milvus, MinIO, and the above services.
- **MFR Service** (`mfr-service/`) — Tiny sidecar for math-formula recognition (Pix2Text ONNX).
- **vLLM-Tune** (`vllm-tune/`) — Standalone shell/Python toolkit for tuning vLLM MoE/FP8 kernel configs on NVIDIA GPUs.
- **Tools / Configs** (`tools/`, `configs/`, `agents/`) — Operational scripts, environment templates, and textbook profiles.

The overall data flow is described in `doc/learning-buddy-status-and-roadmap.md` and `doc/corpus-three-layer-ops.md`: raw inputs (photos, PDFs, audio) are processed by C2/API into structured records (A layer), combined with learning memory (B layer) into an A+ personalized knowledge graph, and then consumed by diagnosis/planning agents and the chat/explanation front-ends.

---

## 2. Repository layout

```
MyLearningBuddy_Model_Workspace/
├── AGENTS.md                  # This file
├── .gitignore
├── agents/                    # Agent-related prompts/configs (sparse)
├── configs/
│   ├── editions/              # Edition configuration files
│   └── textbook_profiles/     # e.g. hujiao_math_hs_v1.json (used by learning-buddy-api)
├── data/                      # Runtime data, cursor-packs, sample images
├── doc/                       # Architecture docs and plans (Chinese/English mixed)
├── DeepTutor/                 # Full tutoring platform (Python + Next.js)
├── exam-ocr-server/           # FastAPI C2 gateway + two Vite frontends
├── learning-buddy-api/        # FastAPI pilot backend
├── learning-buddy-c2/        # Docker Compose for Spark dual-Docker stack
├── mfr-service/              # Math formula recognition microservice
├── models/                   # Symlink → ../opt/models (host model weights)
├── notebooks/                # Jupyter notebooks (empty in current checkout)
├── scripts/                  # Root-level scripts (empty in current checkout)
├── test-images/              # OCR test images
├── tools/                    # DGX/Spark operational scripts and env.example
└── vllm-tune/                # vLLM kernel tuning CLI
```

Key configuration files per subsystem:

| Subsystem | Main config files |
|-----------|-------------------|
| DeepTutor | `DeepTutor/pyproject.toml`, `DeepTutor/docker-compose.yml` (+ `docker-compose.dev.yml`, `docker-compose.ghcr.yml`, `compose.yaml`), `DeepTutor/Dockerfile` (+ `Dockerfile.runner` for the sandbox sidecar), `DeepTutor/web/package.json`, `DeepTutor/web/next.config.js` |
| Learning Buddy API | `learning-buddy-api/requirements.txt`, `learning-buddy-api/Dockerfile` (+ `Dockerfile.incremental`), `learning-buddy-api/app/config.py` |
| C2 Gateway | `exam-ocr-server/requirements.txt`, `exam-ocr-server/Dockerfile` (+ `Dockerfile.fromsource`), `exam-ocr-server/frontend/package.json`, `exam-ocr-server/studio-ui/package.json`, `exam-ocr-server/app/config.py` |
| C2 Stack | `learning-buddy-c2/docker-compose.yml`, `learning-buddy-c2/.env.example` |
| MFR Service | `mfr-service/requirements.txt`, `mfr-service/Dockerfile`, `mfr-service/server.py` |
| vLLM-Tune | `vllm-tune/config.json`, `vllm-tune/vllm-tune.sh`, `vllm-tune/tune-moe.sh`, `vllm-tune/tune-fp8.sh` |

There is **no root** `pyproject.toml`, `package.json`, `Makefile`, or `Cargo.toml`.

---

## 3. Technology stack

### DeepTutor
- **Python** 3.11+ (targets 3.11–3.14; 3.14 best-effort).
- **Backend:** FastAPI + Uvicorn, Pydantic v2, aiosqlite/PocketBase for persistence, LlamaIndex for RAG.
- **Frontend:** Next.js 16 + React 19 + Tailwind CSS + TypeScript.
- **LLM/embedding:** OpenAI-compatible clients, Anthropic, DashScope, Perplexity, etc. (see `pyproject.toml` dependencies and `requirements/`).
- **Sandbox:** Optional hardened `sandbox-runner` sidecar for untrusted code execution.

### Learning Buddy API
- **Python** 3.12 (Docker base image is `python:3.12-slim-bookworm`).
- **Framework:** FastAPI + Uvicorn, Pydantic v2, Pydantic Settings.
- **Database:** PostgreSQL via SQLAlchemy 2.0 (with `psycopg`), fallback/legacy SQLite file.
- **Vector DB:** Milvus (primary) or Qdrant (fallback).
- **Object store:** MinIO.
- **Cache/queues:** Redis (graph, import, edge, scout, publish queues).
- **RAG:** sentence-transformers, llama-index-core, hybrid dense + sparse + RRF + rerank.
- **Graph:** LangGraph (`app/graph/`).
- **LLM:** vLLM OpenAI-compatible endpoint (Qwen3.6-35B-NVFP4).
- **Math bridge:** Node.js module `app/math/` using `@plurimath/plurimath` and `mathjax-full`.

### Exam OCR Server / C2 Gateway
- **Python** 3.12 + FastAPI.
- **Frontends:** two Vite apps (`frontend/` test UI, `studio-ui/` corpus studio).
- **Storage:** MinIO, Redis sessions.
- **Proxy:** OpenAI-compatible `/v1/*` LAN proxy to vLLM.

### C2 Stack (learning-buddy-c2)
- **Docker Compose** with `docker-compose.yml` and `.env`.
- **Containers:** vLLM (custom `ghcr.io/spark-arena/dgx-vllm-eugr-nightly`), `buddy-api`, `buddy-worker`, `c2-gateway`, `mfr`, PostgreSQL, Redis, Milvus, etcd, MinIO, optional Qdrant, optional NeMo NIM embed/rerank, plus `clash-relay` (socat relay) and `nvcr-cache` (local registry mirror) helper services.
- **GPU:** Requires NVIDIA GPU runtime (DGX Spark / GB10). Model weights expected under `~/opt/models` (symlinked from `/opt/models`).

### MFR Service
- **Python** 3.12 + FastAPI.
- **ONNX:** Hugging Face Optimum `ORTModelForVision2Seq` with `breezedeus/pix2text-mfr`.

### vLLM-Tune
- **Bash** + **Python** 3. CLI orchestrates tuning inside a running vLLM Docker container.
- Uses `docker exec`, `tmux`, `jq`, and `rsync` for distributed mode.

---

## 4. Build and run commands

### 4.1 DeepTutor

Install (full app with Web assets):

```bash
cd DeepTutor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
# optional extras (see [project.optional-dependencies] in pyproject.toml for the full list)
pip install -e ".[partners]"     # IM channel SDKs + MCP
pip install -e ".[dev]"          # test tools
pip install -e ".[server]"       # API server dependencies
pip install -e ".[all]"          # everything
```

CLI-only install:

```bash
pip install -e ./packaging/deeptutor-cli
```

Run CLI:

```bash
deeptutor init
deeptutor chat
deeptutor run deep_solve "证明 n^3-n 能被 6 整除" -l zh -t rag --kb math-textbook
```

Run backend directly:

```bash
python -m uvicorn deeptutor.api.main:app --host 0.0.0.0 --port 8001
```

Run frontend (dev):

```bash
cd web
npm install --legacy-peer-deps
npm run dev
```

Docker Compose:

```bash
# Production-like all-in-one container
cd DeepTutor
python scripts/docker_compose.py up -d

# Development with hot reload
python scripts/docker_compose.py -f docker-compose.yml -f docker-compose.dev.yml up
```

### 4.2 Learning Buddy API

Local run (the `.venv` already exists in the checkout):

```bash
cd learning-buddy-api
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8100
```

Run background worker:

```bash
python -m app.jobs.worker
```

The service is normally built and run via the `learning-buddy-c2` Docker Compose stack.

### 4.3 Exam OCR Server / C2 Gateway

```bash
cd exam-ocr-server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The static test UI is served at `/test/` and the studio UI at `/studio/` when the built `static/` directory exists.

### 4.4 Learning Buddy C2 (full Spark stack)

```bash
cd learning-buddy-c2
cp .env.example .env          # edit secrets and model path
# Start vLLM first (cold start 5–15 min)
docker compose up -d vllm
# Wait until vLLM responds
curl -s http://127.0.0.1:8088/v1/models
# Start the rest
docker compose up -d
```

Quick health checks:

```bash
./scripts/smoke-c2.sh
C2_BASE_URL=http://192.168.1.10:8000 ./scripts/acceptance-c2.sh
```

Other scripts in `learning-buddy-c2/scripts/`: `acceptance-pilot.sh`, `acceptance-stack.sh`, `start-vllm-when-ready.sh`, `supervise-c2-deploy.sh`, `deploy-vllm-tune.sh`, `cleanup-test-students.sh`.

Host port map (from `docker-compose.yml`): c2-gateway `8000:8000`, buddy-api `8100:8100`, vLLM `127.0.0.1:8088:8088`, mfr `127.0.0.1:8503:8503`, NeMo NIM embed `127.0.0.1:8010:8000`, NeMo NIM rerank `127.0.0.1:8011:8000`. Postgres/Redis/Milvus/MinIO are internal to the compose network.

### 4.5 MFR Service

```bash
cd mfr-service
docker build -t mfr-service .
# or directly
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8503
```

### 4.6 vLLM-Tune

```bash
cd vllm-tune
./vllm-tune.sh Qwen/Qwen3.6-35B-A3B-FP8 --tp 2 --dry-run
./vllm-tune.sh Qwen/Qwen3.6-35B-A3B-FP8 --tp 2 --deploy
```

---

## 5. Code organization

### 5.1 DeepTutor (`DeepTutor/deeptutor`)

- `api/` — FastAPI app, routers, and utility middleware.
- `agents/` — Domain agents: chat, question, research, math animator, vision solver, notebook, etc.
- `capabilities/` — High-level capability definitions (deep solve, mastery, explore context, Obsidian, subagent).
- `core/` — Core agentic primitives.
- `knowledge/`, `memory/` — Knowledge-base and long-term memory systems.
- `partners/` — IM channel integrations (Telegram, WeCom, Lark, DingTalk, Slack, QQ, Matrix, Zulip, etc.).
- `runtime/` — Orchestrator, registry (tools, capabilities), bootstrap.
- `services/` — Shared services: LLM, RAG, embedding, search, parsing, sandbox, skill hub, subagent, voice, video, imagegen, settings, session, storage.
- `tools/` — Built-in tools available to agents (RAG, web search, code execution, reason, ask_user, etc.).
- `utils/` — Utilities (network, JSON parsing, archive extraction, document validation).
- `web/` — Next.js frontend.
- `deeptutor_cli/` — CLI package source; `deeptutor_web/` is a thin shim package. `packaging/deeptutor-cli/` builds the CLI-only distribution.
- `SKILL.md` — Agent-facing skill doc for driving DeepTutor through the CLI.

### 5.2 Learning Buddy API (`learning-buddy-api/app`)

- `main.py` — FastAPI entry point; registers all routers.
- `config.py` — `Settings` from environment variables (Pydantic Settings).
- `auth.py` — Bearer-token middleware for admin and API tokens.
- `routes/` — FastAPI routers: health, admin, students, ingest, homework, questions, mastery, wrong_book, corpus_admin, editions_admin, import35b, rag, math, graph_runs, mineru_settings.
- `agents/` — Evaluation agents and tracing: `eval_agent.py`, `eval_runner.py`, `eval_bank.py`, `eval_trace.py`, `eval_file_logger.py`, `diagnosis_planning.py`, `observer_agent.py`, `rag_validate.py`.
- `services/` — Business logic: corpus import/publish/reprocess, textbook TOC deepening, PDF import, homework evaluation, wrong book, editions, IR page fusion, MinerU adapter, etc.
- `import_35b/` — Experimental 35B vision-to-blueprint import pipeline (layout, page pipeline, schemas, caselaw, cursor packs).
- `rag/` — Retriever, vector stores (Milvus/Qdrant), RRF, rerank, keyword scoring, graph expansion.
- `graph/` — LangGraph nodes/runner/state for the evaluation graph.
- `db/` — SQLAlchemy models and session helpers.
- `jobs/worker.py` — Redis-backed worker for graph, edge, import, and scout jobs.
- `clients/` — vLLM client, Redis client, MinIO client.
- `math/` — Node.js math normalization/enrichment bridge.
- `schemas/` — Pydantic request/response models.
- `util/` — Shared helpers; `static/` — bundled static assets.

### 5.3 Exam OCR Server (`exam-ocr-server/app`)

- `main.py` — FastAPI app; mounts routers and static UIs.
- `config.py` — Environment settings.
- `routes.py` — Core OCR routes (`/api/v1/system/health`, `/api/v1/ocr/page`).
- `companion/` — Companion session pipeline: frames, per-question timing, image diff, merge, question-to-frame mapping, service, routes.
- `studio/` — Studio import routes and photo import helpers.
- `openai_proxy.py` — LAN `/v1/*` proxy to vLLM.
- `vllm_client.py` — Typed vLLM client with concurrency limit.
- `buddy_client.py` — Client to `learning-buddy-api`.
- `image_preprocess.py`, `image_utils.py`, `ocr_refine.py`, `math_enrich.py` — Vision and post-processing helpers.
- `storage/minio_client.py` — MinIO upload helpers.
- `prompts.py` — Prompt templates for page analysis.
- `../scripts/` — Acceptance/diagnostic scripts (`acceptance-companion.sh`, `acceptance-companion-m1.sh`, `acceptance-ocr-refine.sh`, replay and pipeline-ablation tools).

### 5.4 vLLM-Tune

- `vllm-tune.sh` — Main orchestrator CLI.
- `tune-moe.sh` / `tune-fp8.sh` — MoE and FP8 kernel tuning loops.
- `dist-tune.py` — Distributed tuning across nodes.
- `lib/common.sh` — Shared utilities (must be kept in sync with both tune scripts).
- `lib/detect.py` — Centralized model architecture and shape detection.
- `mod/run.sh` — Container mod for persistent config deployment.
- `configs/` — Pre-shipped and generated kernel configs by model slug and TP size.

---

## 6. Development conventions

### 6.1 Python

- **DeepTutor:** Python 3.11+; Black/Ruff line length **100**, double quotes, space indentation. Import sorting via Ruff (`isort` profile), known first-party packages: `deeptutor`, `deeptutor_cli`, `scripts`.
- **Learning Buddy / C2 / MFR:** Python 3.12; generally follow the same 100-character line length and Black-compatible formatting used in existing files, though only DeepTutor has a formal `pyproject.toml` lint config.
- Use `from __future__ import annotations` in new Python files where appropriate.
- Keep imports grouped: stdlib, third-party, first-party.
- Prefer `pathlib` for paths; existing code uses `os.path` in some places and is allowed in DeepTutor by Ruff config.
- Type hints are optional but encouraged; mypy is relaxed in DeepTutor and not currently enforced in the other services.

### 6.2 JavaScript / TypeScript

- **DeepTutor web:** Next.js 16, React 19, TypeScript 5, Tailwind CSS, ESLint 9. Run `npm run lint` and `npm run test:node`.
- **C2 frontends:** Vite + TypeScript (`frontend/`, `studio-ui/`). Build with `npm run build`.

### 6.3 Shell / Bash

- vLLM-Tune and C2 scripts use `set -euo pipefail`.
- Error output goes to `stderr` (`echo ... >&2`).
- Environment variables override defaults; do not hardcode host-specific paths.

### 6.4 Configuration

- **DeepTutor:** Runtime settings live under `data/user/settings/` as JSON/YAML files (`system.json`, `auth.json`, `integrations.json`, `model_catalog.json`, `main.yaml`, `agents.yaml`). The Docker entrypoint exports these as env vars.
- **Learning Buddy / C2:** Docker Compose reads `.env` from `learning-buddy-c2/`. Copy `learning-buddy-c2/.env.example` to `.env` and change defaults.
- **Tools:** See `tools/env.example` for optional API keys (NGC, Hugging Face, GitHub, Context7, Cursor).

### 6.5 Pre-commit

DeepTutor has `.pre-commit-config.yaml`:

```bash
cd DeepTutor
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

The other subprojects do not have pre-commit configs; rely on manual lint and CI.

---

## 7. Testing instructions

### 7.1 DeepTutor

```bash
cd DeepTutor
# Python tests
pytest tests deeptutor/learning/tests
# Lint / format
ruff check .
ruff format --check .
# Frontend node tests
cd web
npm ci --legacy-peer-deps
npm run test:node
```

GitHub Actions (`DeepTutor/.github/workflows/tests.yml`) runs these jobs on Python 3.11/3.12/3.13 (3.14 best-effort): lint, web tests, import check, Python tests.

### 7.2 Learning Buddy API

```bash
cd learning-buddy-api
source .venv/bin/activate
pytest tests/
```

Tests are primarily unit/integration tests using `unittest.mock` and do not require a live GPU or database by default. Some tests may require a running vLLM/Milvus/MinIO; check individual test files for markers/skips.

### 7.3 Exam OCR Server

```bash
cd exam-ocr-server
pytest tests/
```

### 7.4 vLLM-Tune

```bash
cd vllm-tune
./tests/test-vllm-tune.sh
```

Offline suite; no Docker or GPU required.

### 7.5 C2 Smoke / Acceptance

```bash
cd learning-buddy-c2
./scripts/smoke-c2.sh
C2_BASE_URL=http://192.168.1.10:8000 ./scripts/acceptance-c2.sh
```

---

## 8. Deployment processes

### 8.1 DeepTutor

- **Docker:** `python scripts/docker_compose.py up -d` builds the `deeptutor` image and an optional `sandbox-runner` sidecar. The image is defined in `DeepTutor/Dockerfile` (multi-stage: frontend builder → Python base → production/development targets).
- **Release:** GitHub Releases trigger:
  - `docker-release.yml` → multi-platform image `ghcr.io/hkuds/deeptutor`.
  - `pypi-release.yml` → wheel `deeptutor` on PyPI.
- **CLI-only package:** Installed locally from `packaging/deeptutor-cli/`, not published to PyPI.

### 8.2 Learning Buddy C2 stack

Deployed on a Docker host with NVIDIA Container Toolkit (e.g. DGX Spark / GB10):

```bash
cd learning-buddy-c2
docker compose pull
docker compose up -d vllm   # wait for model load
docker compose up -d
```

Required host-side setup:

1. Model weights at `~/opt/models/Qwen3.6-35B-A3B-NVFP4` (or `/opt/models` via symlink).
2. Pull or tag the vLLM image: `ghcr.io/spark-arena/dgx-vllm-eugr-nightly:latest`.
3. `cp .env.example .env` and set `MODELS_HOST_PATH`, tokens, and mirror/proxy options.

### 8.3 vLLM-Tune deployment

```bash
cd vllm-tune
./vllm-tune.sh Qwen/Qwen3.6-35B-A3B-FP8 --tp 2 --deploy
# To persist across container restarts, sync to the spark-vllm-docker mod:
./vllm-tune.sh Qwen/Qwen3.6-35B-A3B-FP8 --tp 2 --sync-mod
```

---

## 9. Security considerations

- **Default tokens:** `learning-buddy-c2/.env.example` ships hardcoded `ADMIN_TOKEN`, `API_TOKEN`, and `VLLM_LAN_TOKEN`. These **must** be changed before any network exposure.
- **Authentication:**
  - `learning-buddy-api` uses an `AuthMiddleware` that requires a Bearer/API token for all routes except `/`, `/openapi.json`, `/docs`, `/redoc`, `/metrics`, and `/api/v1/system/health`. Admin routes additionally require `ADMIN_TOKEN`.
  - `exam-ocr-server` `/v1/*` LAN proxy requires `VLLM_LAN_TOKEN` when enabled.
  - DeepTutor auth is disabled by default; CORS is permissive in that mode. Enable auth explicitly in `data/user/settings/auth.json`.
- **Sandbox:** DeepTutor routes untrusted code execution to a dedicated `sandbox-runner` container when `DEEPTUTOR_SANDBOX_RUNNER_URL` is set. Do not run untrusted code directly in the main app process in production.
- **Secrets:** Use `.env` files and Docker Compose secrets; never commit real API keys or tokens. The `detect-secrets` pre-commit hook is configured in DeepTutor.
- **Network:** The C2 gateway exposes the OpenAI proxy and test UI on `0.0.0.0:8000`; bind to a trusted LAN only.
- **GPU runtime:** C2 services request `gpus: all`; ensure the host Docker daemon is locked down appropriately.

---

## 10. Common tasks and entry points

| Task | Command / File |
|------|----------------|
| Start the full C2 stack | `cd learning-buddy-c2 && docker compose up -d` |
| Check C2 health | `curl -s http://127.0.0.1:8000/api/v1/system/health` |
| Check vLLM | `curl -s http://127.0.0.1:8088/v1/models` |
| Check Buddy API | `curl -s http://127.0.0.1:8100/api/v1/system/health` |
| Run Learning Buddy worker | `cd learning-buddy-api && python -m app.jobs.worker` |
| Run DeepTutor CLI | `cd DeepTutor && deeptutor chat` |
| Run DeepTutor tests | `cd DeepTutor && pytest tests deeptutor/learning/tests` |
| Run C2 smoke test | `cd learning-buddy-c2 && ./scripts/smoke-c2.sh` |
| Tune vLLM kernels | `cd vllm-tune && ./vllm-tune.sh <model> --tp 2` |
| Backup Buddy data | `tools/backup-buddy-data.sh` |
| Setup China mirrors | `tools/setup-china-mirrors.sh` (and system variant) |

---

## 11. What to avoid

- **Do not** run the C2 stack without changing the default tokens in `.env`.
- **Do not** enable `DEEPTUTOR_SANDBOX_ALLOW_SUBPROCESS` in the main DeepTutor container without the `sandbox-runner` sidecar.
- **Do not** rename or merge vLLM-Tune config JSON files; vLLM matches them by exact filename pattern.
- **Do not** mix the 35B import queue (`import35b_queue`) with the legacy MinerU import queue; they are intentionally isolated.
- **Do not** modify pre-shipped vLLM-Tune configs by hand; update them by re-running tuning.
- **Do not** commit API keys or model weights to the repository.

---

## 12. References

- `DeepTutor/README.md` — DeepTutor full documentation.
- `DeepTutor/deeptutor_cli/README.md` — DeepTutor CLI commands.
- `learning-buddy-c2/README.md` — Spark dual-Docker deployment.
- `exam-ocr-server/README.md` — C2 gateway API.
- `vllm-tune/README.md` / `vllm-tune/AGENTS.md` — vLLM tuning details.
- `doc/learning-buddy-status-and-roadmap.md` — Overall architecture and roadmap.
- `doc/corpus-three-layer-ops.md` — Corpus operations contract.
