#!/usr/bin/env bash
# Pull & start NeMo embed/rerank NIM on DGX Spark (China-friendly).
#
# Download strategy (fastest → slowest):
#   1. NIM_EMBED_IMAGE / NIM_RERANK_IMAGE set to a domestic registry (Alibaba PAI 等) → docker pull 直连
#   2. nvcr pull-through cache (127.0.0.1:5555) + crane parallel layer copy → 经 Clash 只拉一次，断点续传
#
# Requires: Clash reachable at CLASH_HOST:7897 (see learning-buddy-c2/.env), NGC_API_KEY
set -euo pipefail

C2_DIR="$(cd "$(dirname "$0")/../learning-buddy-c2" && pwd)"
ENV_FILE="${C2_DIR}/.env"
PULL_DIR="${HOME}/.cache/nim"
REGISTRY_CONFIG="${PULL_DIR}/registry-config.yml"
REGISTRY_TPL="${C2_DIR}/configs/nvcr-pull-through.yml.tpl"
CACHE_HOST="${NIM_CACHE_HOST:-127.0.0.1:5555}"
SKOPEO_IMAGE="${SKOPEO_IMAGE:-quay.io/skopeo/stable}"

DEFAULT_EMBED="nvcr.io/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:1.5.0"
DEFAULT_RERANK="nvcr.io/nim/nvidia/llama-3.2-nv-rerankqa-1b-v2:1.5.0"

if [ -f "${ENV_FILE}" ]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

EMBED_IMAGE="${NIM_EMBED_IMAGE:-${DEFAULT_EMBED}}"
RERANK_IMAGE="${NIM_RERANK_IMAGE:-${DEFAULT_RERANK}}"

if [ -z "${NGC_API_KEY:-}" ]; then
  echo "ERROR: NGC_API_KEY is empty. Add to ${ENV_FILE}"
  exit 1
fi

mkdir -p "${PULL_DIR}"

use_nvcr_cache() {
  case "$1" in
    nvcr.io/*) return 0 ;;
    *) return 1 ;;
  esac
}

need_proxy() {
  use_nvcr_cache "${EMBED_IMAGE}" || use_nvcr_cache "${RERANK_IMAGE}"
}

PROXY_URL="${HTTP_PROXY:-${HTTPS_PROXY:-http://${CLASH_HOST:-127.0.0.1}:7897}}"

if need_proxy; then
  export HTTP_PROXY="${PROXY_URL}"
  export HTTPS_PROXY="${PROXY_URL}"
  export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,192.168.0.0/16}"
  echo "== proxy check (${PROXY_URL}) =="
  if ! curl -sf --connect-timeout 5 -x "${PROXY_URL}" https://www.google.com >/dev/null; then
    echo "ERROR: proxy unreachable. Start Clash or set HTTP_PROXY in ${ENV_FILE}."
    exit 1
  fi
fi

write_registry_config() {
  sed \
    -e "s|__NGC_USER__|\$oauthtoken|g" \
    -e "s|__NGC_KEY__|${NGC_API_KEY}|g" \
    "${REGISTRY_TPL}" > "${REGISTRY_CONFIG}"
  chmod 600 "${REGISTRY_CONFIG}"
}

start_nvcr_cache() {
  echo "== start nvcr pull-through cache (${CACHE_HOST}, blobs cached on disk) =="
  write_registry_config
  cd "${C2_DIR}"
  docker compose --profile nemo up -d nvcr-cache
  for _ in $(seq 1 30); do
    if curl -sf "http://${CACHE_HOST}/v2/" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "ERROR: nvcr-cache not ready on ${CACHE_HOST}"
  docker logs nvcr-cache 2>&1 | tail -20
  return 1
}

start_proxy_relay() {
  echo "== start clash-relay (containers :17897 -> Clash ${CLASH_HOST:-127.0.0.1}:7897) =="
  cd "${C2_DIR}"
  docker compose --profile nemo up -d clash-relay
}

cache_ref() {
  local ref="$1"
  local path="${ref#nvcr.io/}"
  echo "${CACHE_HOST}/${path}"
}

pull_image() {
  local ref="$1"
  local name="$2"
  local marker="${PULL_DIR}/.${name}.loaded"

  if [ -f "${marker}" ] || docker image inspect "${ref}" >/dev/null 2>&1; then
    echo "== skip pull (already local): ${ref} =="
    return 0
  fi

  if use_nvcr_cache "${ref}"; then
    local src
    src="$(cache_ref "${ref}")"
    echo "== pull via local cache ${src} -> ${ref} =="
    echo "   (blobs cached under nvcr-cache-data; retries skip finished layers)"
    docker pull "${SKOPEO_IMAGE}" >/dev/null
    local attempt=1
    while [ "${attempt}" -le 8 ]; do
      echo "   attempt ${attempt}/8"
      if docker run --rm --network host \
        -v /var/run/docker.sock:/var/run/docker.sock \
        "${SKOPEO_IMAGE}" \
        copy \
        --src-tls-verify=false \
        "docker://${src}" \
        "docker-daemon:${ref}"; then
        touch "${marker}"
        return 0
      fi
      attempt=$((attempt + 1))
      sleep 10
    done
    echo "ERROR: pull failed for ${ref}"
    return 1
  fi

  echo "== docker pull (domestic mirror): ${ref} =="
  docker pull "${ref}"
  touch "${marker}"
}

echo "== docker login nvcr.io =="
echo "${NGC_API_KEY}" | docker login nvcr.io --username '$oauthtoken' --password-stdin

if need_proxy; then
  start_nvcr_cache
fi

pull_image "${EMBED_IMAGE}" "nemo-embed"
pull_image "${RERANK_IMAGE}" "nemo-rerank"

start_proxy_relay

echo "== start nemo profile =="
cd "${C2_DIR}"
docker compose --profile nemo up -d nemo-embed nemo-rerank

echo "== wait for NIM health =="
for _ in $(seq 1 60); do
  embed_ok=0
  rerank_ok=0
  curl -sf http://127.0.0.1:8010/v1/models >/dev/null && embed_ok=1
  curl -sf http://127.0.0.1:8011/v1/models >/dev/null && rerank_ok=1
  if [ "${embed_ok}" = 1 ] && [ "${rerank_ok}" = 1 ]; then
    echo "NeMo embed + rerank ready"
    exit 0
  fi
  sleep 10
done

echo "WARN: NIM health timeout — check: docker logs nemo-embed nemo-rerank"
exit 1
