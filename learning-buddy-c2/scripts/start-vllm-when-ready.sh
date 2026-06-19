#!/usr/bin/env bash
# Wait for model + vLLM image, then start vllm and restart c2-gateway.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL_DIR="${MODELS_HOST_PATH:-$HOME/opt/models}/Qwen3.6-35B-A3B-NVFP4"
DOWNLOAD_LOG="${MODEL_DOWNLOAD_LOG:-$HOME/opt/models/download.log}"
SHARD_1="$MODEL_DIR/model-00001-of-00003.safetensors"
SHARD_2="$MODEL_DIR/model-00002-of-00003.safetensors"
SHARD_3="$MODEL_DIR/model-00003-of-00003.safetensors"

model_ready() {
  if [[ -f "$DOWNLOAD_LOG" ]] && grep -q 'DOWNLOAD_COMPLETE' "$DOWNLOAD_LOG"; then
    return 0
  fi
  [[ -f "$SHARD_1" && -f "$SHARD_2" && -f "$SHARD_3" ]]
}

echo "Waiting for model (3 safetensors shards or DOWNLOAD_COMPLETE) at $MODEL_DIR ..."
while ! model_ready; do
  present=0
  [[ -f "$SHARD_1" ]] && ((present++)) || true
  [[ -f "$SHARD_2" ]] && ((present++)) || true
  [[ -f "$SHARD_3" ]] && ((present++)) || true
  echo "  progress: ${present}/3 shards present"
  sleep 60
done
echo "Model ready"

echo "Waiting for vllm/vllm-openai:cu130-nightly ..."
while ! docker image inspect vllm/vllm-openai:cu130-nightly &>/dev/null; do
  sleep 30
done

docker compose up -d vllm
echo "Waiting for vLLM /v1/models (cold start may take 10+ min) ..."
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8088/v1/models >/dev/null 2>&1; then
    echo "vLLM ready"
    docker compose up -d c2-gateway
    curl -s http://127.0.0.1:8000/api/v1/system/health | python3 -m json.tool
    exit 0
  fi
  sleep 30
done
echo "vLLM did not become ready in time; check: docker logs -f vllm-qwen36-35b"
exit 1
