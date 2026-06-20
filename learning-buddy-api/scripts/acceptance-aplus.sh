#!/usr/bin/env bash
# A⁺ aggregate harness: dwell_ms + wrong_rate → fluency on MasteryView.
set -euo pipefail

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
STUDENT="${STUDENT:-student-01}"
API_TOKEN="${API_TOKEN:-change-me-api}"
MAX_WAIT="${MAX_WAIT:-120}"

auth=(-H "Authorization: Bearer ${API_TOKEN}")

echo "== A⁺ aggregate acceptance =="

HW_ID="aplus-accept-$(date +%s)"
INGEST=$(curl -sf -X POST "${BUDDY_URL}/api/v1/students/${STUDENT}/ingest/homework" \
  "${auth[@]}" -H "Content-Type: application/json" \
  -d "{
    \"id\": \"${HW_ID}\",
    \"session_id\": \"aplus-session\",
    \"page_id\": \"p1\",
    \"finalized_at\": \"2026-06-20T12:00:00+00:00\",
    \"source_frame_ids\": [\"f1\"],
    \"parts\": [
      {\"kind\": \"question\", \"id\": \"1\", \"print_text\": \"已知全集 U=(-∞, 1)，集合 A=(-1, 1)，则 A 的补集为\", \"handwriting\": \"(-∞,-1]\"},
      {\"kind\": \"question\", \"id\": \"2\", \"print_text\": \"不等式 (3x-1)/(x+1) > 0 的解集为\", \"handwriting\": \"x<-1 或 x>1/3\"}
    ],
    \"meta\": {
      \"subject\": \"math\",
      \"grade_band\": \"hs\",
      \"question_timings\": [
        {\"question_id\": \"1\", \"duration_ms\": 90000, \"duration_source\": \"acceptance\"},
        {\"question_id\": \"2\", \"duration_ms\": 240000, \"duration_source\": \"acceptance\"}
      ]
    }
  }")

Q2=$(echo "$INGEST" | python3 -c "import sys,json; qs=json.load(sys.stdin)['questions']; print([q['id'] for q in qs if q['ordinal']=='2'][0])")
curl -sf -X PATCH "${BUDDY_URL}/api/v1/students/${STUDENT}/questions/${Q2}" \
  "${auth[@]}" -H "Content-Type: application/json" -d '{"is_wrong": true}' >/dev/null

echo "Ingested ${HW_ID}, waiting for graph + aplus_aggregate..."
RUNS=""
for _ in $(seq 1 "${MAX_WAIT}"); do
  RUNS=$(curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/students/${STUDENT}/graph-runs")
  done=$(echo "$RUNS" | HW_ID="${HW_ID}" python3 -c "
import os, sys, json
hw = os.environ['HW_ID']
for r in json.load(sys.stdin):
    if r.get('homework_id') == hw and r.get('status') in ('succeeded', 'failed'):
        print(r.get('status'))
        for s in r.get('steps') or []:
            if s.get('node') == 'aplus_aggregate':
                print('APLUS:' + s.get('detail', ''))
        break
" 2>/dev/null || true)
  if [ -n "$done" ]; then
    break
  fi
  sleep 1
done

status=$(echo "$done" | head -1)
aplus=$(echo "$done" | grep '^APLUS:' | cut -d: -f2-)
if [ "$status" != "succeeded" ]; then
  echo "FAIL: graph run status=${status:-timeout}"
  exit 1
fi
if [ -z "$aplus" ]; then
  echo "FAIL: missing aplus_aggregate step"
  exit 1
fi
echo "OK: aplus_aggregate — ${aplus}"

MASTERY=$(curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/students/${STUDENT}/mastery")
echo "$MASTERY" | HW_ID="${HW_ID}" python3 -c "
import os, sys, json
hw = os.environ['HW_ID']
rows = json.load(sys.stdin)
if not rows:
    print('FAIL: empty mastery')
    sys.exit(1)
# At least one kp touched by this homework should expose fluency fields
has_fluency = any('fluency' in r and 'fluency_ratio' in r for r in rows)
if not has_fluency:
    print('FAIL: mastery missing fluency fields')
    sys.exit(1)
rational = next((r for r in rows if r.get('kp_id') == 'math.hs.inequality.rational'), None)
if rational:
    print('OK: rational kp fluency_ratio=', rational.get('fluency_ratio'), 'fluency=', rational.get('fluency'), 'wrong_rate=', rational.get('wrong_rate'))
else:
    print('OK: mastery fluency fields present (', len(rows), 'kps )')
"

echo "PASS: acceptance-aplus"
