#!/usr/bin/env bash
# Wait for model + vLLM image, then start vllm and restart c2-gateway.
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL_DIR="${MODELS_HOST_PATH:-$HOME/opt/models}/Qwen3.8-27B-NVFP4"
DOWNLOAD_LOG="${MODEL_DOWNLOAD_LOG:-$HOME/opt/models/download.log}"
WEIGHTS="$MODEL_DIR/model.safetensors"

model_ready() {
  [[ -f "$MODEL_DIR/.download-complete" ]]
}

echo "Waiting for model (.download-complete) at $MODEL_DIR ..."
while ! model_ready; do
  if [[ -f "$WEIGHTS" ]]; then
    echo "  weights file present, waiting for .download-complete"
  else
    echo "  weights file not present yet"
  fi
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
