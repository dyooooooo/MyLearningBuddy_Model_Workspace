#!/usr/bin/env bash
# Supervise model download, vLLM image pull, and stack startup. Restarts dead workers.
set -euo pipefail

COMPOSE_DIR="${COMPOSE_DIR:-$HOME/MyLearningBuddy_Model_Workspace/learning-buddy-c2}"
MODEL_DIR="${MODELS_HOST_PATH:-$HOME/opt/models}/Qwen3.8-27B-NVFP4"
VENV_PY="${MODEL_VENV_PY:-$HOME/opt/models/.venv/bin/python}"
DOWNLOAD_LOG="${MODEL_DOWNLOAD_LOG:-$HOME/opt/models/download.log}"
PULL_LOG="${VLLM_PULL_LOG:-$HOME/opt/models/vllm-pull.log}"
SUP_LOG="${SUPERVISOR_LOG:-$HOME/opt/models/supervisor.log}"
LOCK_FILE="${SUPERVISOR_LOCK:-$HOME/opt/models/supervisor.lock}"
VLLM_IMAGE="ghcr.io/spark-arena/dgx-vllm-eugr-nightly:latest"
VLLM_SWR_IMAGE="swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/spark-arena/dgx-vllm-eugr-nightly:latest-linuxarm64"
INTERVAL="${SUPERVISOR_INTERVAL:-60}"
STALL_MINUTES="${STALL_MINUTES:-15}"

SHARD_1="$MODEL_DIR/model-00001-of-00003.safetensors"
SHARD_2="$MODEL_DIR/model-00002-of-00003.safetensors"
SHARD_3="$MODEL_DIR/model-00003-of-00003.safetensors"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$SUP_LOG"
}

model_ready() {
  if [[ -f "$DOWNLOAD_LOG" ]] && grep -q 'DOWNLOAD_COMPLETE' "$DOWNLOAD_LOG"; then
    return 0
  fi
  [[ -f "$SHARD_1" && -f "$SHARD_2" && -f "$SHARD_3" ]]
}

shard_count() {
  local n=0
  [[ -f "$SHARD_1" ]] && ((n++)) || true
  [[ -f "$SHARD_2" ]] && ((n++)) || true
  [[ -f "$SHARD_3" ]] && ((n++)) || true
  echo "$n"
}

vllm_image_ready() {
  docker image inspect "$VLLM_IMAGE" &>/dev/null
}

download_pids() {
  # Match venv python workers downloading this model (per-shard or snapshot).
  pgrep -f "${VENV_PY}.*(snapshot_download|hf_hub_download).*Qwen3.8-27B-NVFP4" 2>/dev/null || true
}

pull_pids() {
  ps -C docker -o pid=,args= 2>/dev/null \
    | awk -v swr="$VLLM_SWR_IMAGE" -v img="$VLLM_IMAGE" \
      '$2 == "pull" && ($3 == img || $3 == swr || index($0, "dgx-vllm-eugr-nightly")) {print $1}' || true
}

kill_duplicates() {
  local pids=()
  local keep
  mapfile -t pids < <(download_pids)
  if ((${#pids[@]} > 1)); then
    keep="${pids[0]}"
    for pid in "${pids[@]:1}"; do
      log "Killing duplicate snapshot_download pid=$pid (keeping $keep)"
      kill "$pid" 2>/dev/null || true
    done
  fi

  mapfile -t pids < <(pull_pids)
  if ((${#pids[@]} > 1)); then
    keep="${pids[0]}"
    for pid in "${pids[@]:1}"; do
      log "Killing duplicate docker pull pid=$pid (keeping $keep)"
      kill "$pid" 2>/dev/null || true
    done
  fi
}

start_download() {
  log "Starting model download (hf-mirror, xet enabled, long timeout)"
  export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
  export HUGGINGFACE_HUB_ENDPOINT="${HUGGINGFACE_HUB_ENDPOINT:-https://hf-mirror.com}"
  export HF_HUB_DOWNLOAD_TIMEOUT="${HF_HUB_DOWNLOAD_TIMEOUT:-3600}"
  nohup env \
    HF_HUB_DOWNLOAD_TIMEOUT="$HF_HUB_DOWNLOAD_TIMEOUT" \
    HF_ENDPOINT="$HF_ENDPOINT" \
    HUGGINGFACE_HUB_ENDPOINT="$HUGGINGFACE_HUB_ENDPOINT" \
    "$VENV_PY" -c "
from huggingface_hub import hf_hub_download
import os
model_dir = '${MODEL_DIR}'
shards = [
    'model-00001-of-00003.safetensors',
    'model-00002-of-00003.safetensors',
    'model-00003-of-00003.safetensors',
]
for name in shards:
    path = os.path.join(model_dir, name)
    if os.path.isfile(path):
        print(f'skip existing {name}')
        continue
    print(f'downloading {name}...')
    hf_hub_download(
        repo_id='nvidia/Qwen3.8-27B-NVFP4',
        filename=name,
        local_dir=model_dir,
    )
    print(f'completed {name}')
missing = [s for s in shards if not os.path.isfile(os.path.join(model_dir, s))]
if missing:
    raise SystemExit(f'incomplete shards: {missing}')
print('DOWNLOAD_COMPLETE')
" >>"$DOWNLOAD_LOG" 2>&1 &
}

start_pull() {
  log "Starting docker pull $VLLM_SWR_IMAGE (Huawei SWR, tag as $VLLM_IMAGE)"
  nohup bash -c "docker pull \"$VLLM_SWR_IMAGE\" && docker tag \"$VLLM_SWR_IMAGE\" \"$VLLM_IMAGE\"" >>"$PULL_LOG" 2>&1 &
}

ensure_download() {
  if model_ready; then
    return 0
  fi
  kill_duplicates
  local pids
  pids=$(download_pids | tr '\n' ' ')
  if [[ -z "${pids// }" ]]; then
    log "No snapshot_download running — restarting"
    start_download
    return 0
  fi

  if [[ -f "$DOWNLOAD_LOG" ]]; then
    local age=$(( $(date +%s) - $(stat -c %Y "$DOWNLOAD_LOG") ))
  else
    local age=999999
  fi
  if (( age > STALL_MINUTES * 60 )); then
    log "download.log stale ${age}s with ${pids} — restarting snapshot_download"
    for pid in $pids; do kill "$pid" 2>/dev/null || true; done
    sleep 2
    start_download
  fi
}

ensure_pull() {
  if vllm_image_ready; then
    return 0
  fi
  kill_duplicates
  local pids
  pids=$(pull_pids | tr '\n' ' ')
  if [[ -z "${pids// }" ]]; then
    log "No docker pull running — restarting"
    start_pull
    return 0
  fi

  if [[ -f "$PULL_LOG" ]]; then
    local age=$(( $(date +%s) - $(stat -c %Y "$PULL_LOG") ))
  else
    local age=999999
  fi
  if (( age > STALL_MINUTES * 60 )); then
    log "vllm-pull.log stale ${age}s with ${pids} — restarting docker pull"
    for pid in $pids; do kill "$pid" 2>/dev/null || true; done
    sleep 2
    start_pull
  fi
}

deploy_stack() {
  log "Model + image ready — starting vLLM"
  cd "$COMPOSE_DIR"
  docker compose up -d vllm

  log "Waiting for vLLM /v1/models (up to 20 min) ..."
  for _ in $(seq 1 40); do
    if curl -sf http://127.0.0.1:8088/v1/models >/dev/null 2>&1; then
      log "vLLM ready — restarting c2-gateway"
      docker compose up -d c2-gateway
      curl -s http://127.0.0.1:8000/api/v1/system/health | tee -a "$SUP_LOG"
      return 0
    fi
    sleep 30
  done
  log "vLLM not ready after 20 min — check: docker logs -f vllm-qwen36-35b"
  return 1
}

acquire_lock() {
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "Another supervisor holds $LOCK_FILE — exiting" >&2
    exit 1
  fi
}

main() {
  mkdir -p "$(dirname "$SUP_LOG")"
  acquire_lock
  log "Supervisor started (interval=${INTERVAL}s)"

  while true; do
    kill_duplicates
    ensure_download
    ensure_pull

    local shards
    shards=$(shard_count)
    if model_ready && vllm_image_ready; then
      if curl -sf http://127.0.0.1:8088/v1/models >/dev/null 2>&1; then
        health=$(curl -s http://127.0.0.1:8000/api/v1/system/health 2>/dev/null || echo '{}')
        if echo "$health" | grep -q '"vllm_reachable": true'; then
          log "Stack healthy — supervisor exiting"
          exit 0
        fi
        log "vLLM up but gateway not healthy — restarting c2-gateway"
        (cd "$COMPOSE_DIR" && docker compose up -d c2-gateway)
      else
        deploy_stack || true
      fi
    else
      log "Waiting: shards=${shards}/3 image=$(vllm_image_ready && echo yes || echo no) dl=$(download_pids | wc -l) pull=$(pull_pids | wc -l)"
    fi

    sleep "$INTERVAL"
  done
}

main "$@"
