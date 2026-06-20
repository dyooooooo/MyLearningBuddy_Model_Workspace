#!/usr/bin/env bash
# M1 companion frontend + gateway: ensure-student, tier, mastery fluency fields.
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8000}"
BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
API_TOKEN="${API_TOKEN:-change-me-api}"
STUDENT="${STUDENT:-student-01}"
M1_STUDENT="${M1_STUDENT:-student-01}"

API="${GATEWAY_URL}/api/v1/companion"
auth=(-H "Authorization: Bearer ${API_TOKEN}")
student_hdr=(-H "X-Student-Id: ${M1_STUDENT}")

echo "== M1 companion acceptance @ ${GATEWAY_URL} =="

for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf "${GATEWAY_URL}/api/v1/system/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -sf "${GATEWAY_URL}/api/v1/system/health" >/dev/null || {
  echo "FAIL: gateway not reachable"
  exit 1
}

echo "== ensure-student (new) =="
curl -sf -X POST "${API}/buddy/ensure-student" \
  "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d "{\"id\":\"${M1_STUDENT}\",\"label\":\"M1 Accept\",\"grade_band\":\"hs\"}" | python3 -m json.tool

echo "== ensure-student (idempotent) =="
curl -sf -X POST "${API}/buddy/ensure-student" \
  "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d "{\"id\":\"${M1_STUDENT}\",\"label\":\"M1 Accept\",\"grade_band\":\"hs\"}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('created') is False, d"

echo "== buddy tier =="
curl -sf "${API}/buddy/tier" "${student_hdr[@]}" | python3 -m json.tool

echo "== ingest homework with timings (buddy direct) =="
HW_ID="m1-hw-$(date +%s)"
curl -sf -X POST "${BUDDY_URL}/api/v1/students/${M1_STUDENT}/ingest/homework" \
  "${auth[@]}" "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d "{
    \"id\": \"${HW_ID}\",
    \"session_id\": \"m1-session\",
    \"page_id\": \"p1\",
    \"finalized_at\": \"2026-06-20T14:00:00+00:00\",
    \"source_frame_ids\": [\"f1\", \"f2\"],
    \"parts\": [
      {\"kind\": \"question\", \"id\": \"1\", \"print_text\": \"解不等式 x²-5x+6>0\", \"handwriting\": \"x<2 或 x>3\", \"bbox_print\": [0.1,0.2,0.9,0.4], \"bbox_handwriting\": [0.1,0.5,0.9,0.7]},
      {\"kind\": \"question\", \"id\": \"2\", \"print_text\": \"已知全集 U=(-∞,1)\", \"handwriting\": \"(-∞,-1]\", \"bbox_print\": [0.1,0.1,0.8,0.3], \"bbox_handwriting\": [0.2,0.4,0.7,0.55]}
    ],
    \"meta\": {
      \"subject\": \"math\",
      \"grade_band\": \"hs\",
      \"question_timings\": [
        {\"question_id\": \"1\", \"duration_ms\": 120000, \"duration_source\": \"acceptance\"},
        {\"question_id\": \"2\", \"duration_ms\": 60000, \"duration_source\": \"acceptance\"}
      ],
      \"question_frame_map\": {\"1\": \"f2\", \"2\": \"f1\"}
    }
  }" | python3 -c "
import sys, json
d = json.load(sys.stdin)
qs = d.get('questions') or []
assert len(qs) >= 1, 'no questions'
q = qs[0]
for key in ('print_text', 'kp_tags'):
    assert key in q, f'missing {key}'
if q.get('bbox_print') is not None:
    assert isinstance(q['bbox_print'], list)
print('OK: ingest questions', len(qs))
"

Q2="${HW_ID}__q__2"
curl -sf -X PATCH "${BUDDY_URL}/api/v1/students/${M1_STUDENT}/questions/${Q2}" \
  "${auth[@]}" "${student_hdr[@]}" -H "Content-Type: application/json" \
  -d '{"is_wrong": true}' >/dev/null

echo "== gateway homework detail (bbox + meta) =="
curl -sf "${API}/homework/${HW_ID}" "${student_hdr[@]}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
meta = d.get('meta') or {}
assert 'question_frame_map' in meta, meta
qs = d.get('questions') or []
assert qs, 'no questions in homework detail'
q = qs[0]
assert 'bbox_print' in q and 'bbox_handwriting' in q
print('OK: homework meta frame_map + bbox keys')
"

echo "== mastery fluency fields =="
curl -sf "${API}/buddy/mastery" "${student_hdr[@]}" | python3 -c "
import sys, json
rows = json.load(sys.stdin)
if not rows:
    print('WARN: empty mastery (graph may still be running)')
else:
    r = rows[0]
    for key in ('fluency', 'fluency_ratio', 'wrong_rate', 'evidence'):
        assert key in r, f'missing {key} in mastery row'
    print('OK: mastery fluency fields present', len(rows), 'kps')
"

echo "== question_frame_map unit (gateway python) =="
if docker ps --format '{{.Names}}' | grep -qx c2-gateway; then
  docker exec -e PYTHONPATH=/app -w /app c2-gateway python3 /app/tests/test_question_frame_map.py
else
  python3 "${BASH_SOURCE%/*}/../tests/test_question_frame_map.py"
fi

echo "PASS: acceptance-companion-m1"
