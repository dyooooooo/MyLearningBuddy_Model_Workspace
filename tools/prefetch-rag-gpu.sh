#!/usr/bin/env bash
# Prefetch bge embed + rerank weights via hf-mirror (fastest on China network).
# Cache: ~/.cache/huggingface (mounted into buddy-api). No NIM / no proxy needed.
set -euo pipefail

C2_DIR="$(cd "$(dirname "$0")/../learning-buddy-c2" && pwd)"
ENV_FILE="${C2_DIR}/.env"
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
EMBED_MODEL="${EMBED_MODEL:-BAAI/bge-small-zh-v1.5}"
RERANK_MODEL="${RERANK_MODEL:-BAAI/bge-reranker-v2-m3}"
EMBED_DEVICE="${EMBED_DEVICE:-cuda}"

if [ -f "${ENV_FILE}" ]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

mkdir -p "${HOME}/.cache/huggingface"

echo "== prefetch RAG GPU models (HF_ENDPOINT=${HF_ENDPOINT}) =="
echo "   embed:  ${EMBED_MODEL}"
echo "   rerank: ${RERANK_MODEL}"

docker compose -f "${C2_DIR}/docker-compose.yml" exec -T \
  -e HF_ENDPOINT="${HF_ENDPOINT}" \
  -e EMBED_MODEL="${EMBED_MODEL}" \
  -e RERANK_MODEL="${RERANK_MODEL}" \
  -e EMBED_DEVICE="${EMBED_DEVICE}" \
  buddy-api python3 - <<'PY'
import os
import torch
from sentence_transformers import CrossEncoder, SentenceTransformer

device = os.environ.get("EMBED_DEVICE", "cuda")
if device == "cuda" and not torch.cuda.is_available():
    device = "cpu"
    print("WARN: CUDA unavailable, prefetch on CPU")

embed = os.environ["EMBED_MODEL"]
rerank = os.environ["RERANK_MODEL"]
print(f"Loading embed {embed} on {device}...")
SentenceTransformer(embed, device=device)
print(f"Loading rerank {rerank} on {device}...")
CrossEncoder(rerank, device=device)
print("Prefetch OK")
PY

echo "== done =="
