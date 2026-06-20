#!/usr/bin/env bash
set -euo pipefail

C2_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "${C2_DIR}"

echo "== Docker compose ps =="
docker compose ps

echo "== Wait for buddy-api health =="
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8100/api/v1/system/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "== Wait for c2-gateway health =="
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/api/v1/system/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

bash "${C2_DIR}/scripts/acceptance-pilot.sh"
echo "PASS: acceptance-stack"
