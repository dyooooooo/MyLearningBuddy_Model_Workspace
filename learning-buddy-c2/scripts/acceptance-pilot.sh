#!/usr/bin/env bash
# Pilot stack acceptance: infra + buddy-api + graph + gateway proxy.
set -euo pipefail

C2_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WS="$(cd "${C2_DIR}/.." && pwd)"
BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8000}"
ADMIN_TOKEN="${ADMIN_TOKEN:-change-me-admin}"
API_TOKEN="${API_TOKEN:-change-me-api}"
STUDENT="${STUDENT:-student-01}"
BENCHMARK_MIN="${BENCHMARK_MIN_TOP1:-0.70}"

auth=(-H "Authorization: Bearer ${API_TOKEN}")
admin=(-H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json")
admin_get=(-H "Authorization: Bearer ${ADMIN_TOKEN}")
student_hdr=(-H "X-Student-Id: ${STUDENT}")

echo "== Ensure pilot student ${STUDENT} =="
EXISTING=$(curl -sf "${admin_get[@]}" "${BUDDY_URL}/api/v1/admin/students" 2>/dev/null || echo "[]")
echo "$EXISTING" | python3 -c "import sys,json; ids=[s['id'] for s in json.load(sys.stdin)]; sys.exit(0 if '${STUDENT}' in ids else 1)" \
  || curl -sf -X POST "${BUDDY_URL}/api/v1/admin/students" "${admin[@]}" \
    -d "{\"id\":\"${STUDENT}\",\"label\":\"Pilot\",\"grade_band\":\"hs\"}" | python3 -m json.tool

echo "== Buddy health (postgres/milvus/redis) =="
HEALTH=$(curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/system/health")
echo "$HEALTH" | python3 -m json.tool
python3 -c "import sys,json; h=json.load(sys.stdin); assert h.get('db_ok'), 'db_ok false'" <<<"$HEALTH"
python3 -c "import sys,json; h=json.load(sys.stdin); vb=h.get('vector_backend','milvus'); ok=h.get('milvus_ok') or h.get('qdrant_ok') or h.get('chroma_ok'); assert ok, f'vector store unhealthy backend={vb}'" <<<"$HEALTH"
python3 -c "import sys,json; h=json.load(sys.stdin); assert h.get('redis_ok'), 'redis_ok false'" <<<"$HEALTH"

echo "== Gateway health (redis/minio) =="
GHEALTH=$(curl -sf "${GATEWAY_URL}/api/v1/system/health")
echo "$GHEALTH" | python3 -m json.tool
python3 -c "import sys,json; h=json.load(sys.stdin); assert h.get('redis_ok'), 'gateway redis_ok false'" <<<"$GHEALTH"

export BUDDY_URL GATEWAY_URL STUDENT API_TOKEN ADMIN_TOKEN
bash "${WS}/learning-buddy-api/scripts/acceptance-buddy-api.sh"
bash "${WS}/learning-buddy-api/scripts/acceptance-graph-m3.sh"
bash "${WS}/learning-buddy-api/scripts/acceptance-eval-agent.sh"
bash "${WS}/learning-buddy-api/scripts/acceptance-aplus.sh"

echo "== Gateway buddy proxy with X-Student-Id =="
curl -sf "${GATEWAY_URL}/api/v1/companion/buddy/mastery" "${student_hdr[@]}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('kp records:', len(d))"

echo "== KP RAG benchmark (math_hs) =="
docker exec buddy-api python3 /app/scripts/benchmark-kp-rag.py --min-top1 "${BENCHMARK_MIN}" \
  || python3 "${WS}/learning-buddy-api/scripts/benchmark-kp-rag.py" --min-top1 "${BENCHMARK_MIN}"

echo "== M1 companion frontend =="
bash "${WS}/exam-ocr-server/scripts/acceptance-companion-m1.sh"

echo "== OCR-refine module smoke (no vLLM) =="
if docker ps --format '{{.Names}}' | grep -qx c2-gateway; then
  docker exec c2-gateway bash /app/scripts/acceptance-ocr-refine.sh
else
  bash "${WS}/exam-ocr-server/scripts/acceptance-ocr-refine.sh"
fi

echo "== OCR-refine golden (print-only, mock refine) =="
if docker ps --format '{{.Names}}' | grep -qx c2-gateway; then
  docker exec c2-gateway bash /app/scripts/acceptance-ocr-refine-golden.sh
else
  bash "${WS}/exam-ocr-server/scripts/acceptance-ocr-refine-golden.sh"
fi

echo "PASS: acceptance-pilot"
