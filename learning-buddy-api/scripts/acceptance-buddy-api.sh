#!/usr/bin/env bash
set -euo pipefail

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8000}"
STUDENT="${STUDENT:-student-01}"
API_TOKEN="${API_TOKEN:-change-me-api}"

auth=(-H "Authorization: Bearer ${API_TOKEN}")
student_hdr=(-H "X-Student-Id: ${STUDENT}")

echo "== Buddy API health =="
curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/system/health" | python3 -m json.tool

echo "== Corpus counts =="
HEALTH=$(curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/system/health")
SYL=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('syllabus_count',0))")
QB=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('question_bank_count',0))")
if [ "$SYL" -lt 20 ] || [ "$QB" -lt 40 ]; then
  echo "WARN: corpus small (syllabus=$SYL bank=$QB); run reindex if needed"
fi

for i in 1 2 3 4 5; do
  if curl -sf "${GATEWAY_URL}/api/v1/system/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "== Ingest sample homework =="
HW_ID="accept-hw-$(date +%s)"
curl -sf -X POST "${BUDDY_URL}/api/v1/students/${STUDENT}/ingest/homework" \
  "${auth[@]}" -H "Content-Type: application/json" \
  -d "{
    \"id\": \"${HW_ID}\",
    \"session_id\": \"accept-session\",
    \"page_id\": \"page-1\",
    \"finalized_at\": \"2026-06-19T12:00:00+00:00\",
    \"source_frame_ids\": [\"f1\"],
    \"parts\": [
      {\"kind\": \"question\", \"id\": \"1\", \"print_text\": \"已知全集 U=(-∞, 1)，集合 A=(-1, 1)，则 A 的补集为\", \"handwriting\": \"(-∞,-1]\"},
      {\"kind\": \"question\", \"id\": \"2\", \"print_text\": \"不等式 (3x-1)/(x+1) > 0 的解集为\", \"handwriting\": \"x<-1 或 x>1/3\"}
    ],
    \"meta\": {\"subject\": \"math\", \"grade_band\": \"hs\"}
  }" | python3 -m json.tool

echo "== Mastery (kp-level) =="
MASTERY=$(curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/students/${STUDENT}/mastery")
echo "$MASTERY" | python3 -m json.tool
KP_COUNT=$(echo "$MASTERY" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
if [ "$KP_COUNT" -lt 1 ]; then
  echo "FAIL: expected at least 1 mastery kp record"
  exit 1
fi

QID="${HW_ID}__q__2"
echo "== Mark question wrong: ${QID} =="
curl -sf -X PATCH "${BUDDY_URL}/api/v1/students/${STUDENT}/questions/${QID}" \
  "${auth[@]}" -H "Content-Type: application/json" \
  -d '{"is_wrong": true}' | python3 -m json.tool

echo "== Gateway buddy proxy =="
curl -sf "${GATEWAY_URL}/api/v1/companion/buddy/mastery" "${student_hdr[@]}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('kp records:', len(d))"

echo "== RAG search =="
curl -sf "${auth[@]}" --get "${BUDDY_URL}/api/v1/rag/search" \
  --data-urlencode "q=分式不等式" \
  --data-urlencode "subject=math" \
  --data-urlencode "grade_band=hs" | python3 -m json.tool | head -30

if [ "${1:-}" = "--benchmark" ]; then
  echo "== KP RAG benchmark =="
  BENCHMARK_MIN="${BENCHMARK_MIN_TOP1:-0.70}"
  docker exec buddy-api python3 /app/scripts/benchmark-kp-rag.py --min-top1 "${BENCHMARK_MIN}" \
    || python3 "$(dirname "$0")/benchmark-kp-rag.py" --min-top1 "${BENCHMARK_MIN}"
fi

echo "PASS: acceptance-buddy-api"
