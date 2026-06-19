#!/usr/bin/env bash
# Deploy vLLM-Tune kernel configs to the running vLLM container (no HF download).
set -euo pipefail

TUNE_HOME="${VLLM_TUNE_HOME:-/home/mylearningbuddy/workspace/vllm-tune}"
CONTAINER="${VLLM_TUNE_CONTAINER:-vllm-qwen36-35b}"
MODEL="${VLLM_TUNE_MODEL:-Qwen/Qwen3.6-35B-A3B-FP8}"
TP="${VLLM_TUNE_TP:-1}"

cd "$TUNE_HOME"
./vllm-tune.sh "$MODEL" --tp "$TP" --mode all --deploy-only -t "$CONTAINER"

echo "Restart vLLM to load tuned kernels:"
echo "  docker restart $CONTAINER"
