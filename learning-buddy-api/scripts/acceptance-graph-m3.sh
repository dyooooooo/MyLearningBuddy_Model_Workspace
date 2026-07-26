#!/usr/bin/env bash
set -euo pipefail

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
STUDENT="${STUDENT:-student-01}"
API_TOKEN="${API_TOKEN:-change-me-api}"
ADMIN_TOKEN="${ADMIN_TOKEN:-change-me-admin}"
MAX_WAIT="${MAX_WAIT:-180}"

auth=(-H "Authorization: Bearer ${API_TOKEN}")
admin=(-H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json")
admin_get=(-H "Authorization: Bearer ${ADMIN_TOKEN}")

echo "== M3 graph acceptance (7 nodes, eval M2a two-phase) =="

echo "== Ensure pilot student ${STUDENT} =="
EXISTING=$(curl -sf "${admin_get[@]}" "${BUDDY_URL}/api/v1/admin/students" 2>/dev/null || echo "[]")
echo "$EXISTING" | python3 -c "import sys,json; ids=[s['id'] for s in json.load(sys.stdin)]; sys.exit(0 if '${STUDENT}' in ids else 1)" \
  || curl -sf -X POST "${BUDDY_URL}/api/v1/admin/students" "${admin[@]}" \
    -d "{\"id\":\"${STUDENT}\",\"label\":\"Graph M3\",\"grade_band\":\"hs\"}" >/dev/null

HW_ID="graph-accept-$(date +%s)"
curl -sf -X POST "${BUDDY_URL}/api/v1/students/${STUDENT}/ingest/homework" \
  "${auth[@]}" -H "Content-Type: application/json" \
  -d "{
    \"id\": \"${HW_ID}\",
    \"session_id\": \"graph-session\",
    \"page_id\": \"p1\",
    \"finalized_at\": \"2026-06-19T14:00:00+00:00\",
    \"source_frame_ids\": [\"f1\"],
    \"parts\": [
      {\"kind\": \"question\", \"id\": \"1\", \"print_text\": \"解不等式 x²-5x+6>0\", \"handwriting\": \"x<2 或 x>3\"}
    ],
    \"meta\": {\"subject\": \"math\", \"grade_band\": \"hs\"}
  }" >/dev/null

echo "Ingested ${HW_ID}, waiting for graph run..."
found=""
for i in $(seq 1 "${MAX_WAIT}"); do
  RUNS=$(curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/students/${STUDENT}/graph-runs")
  match=$(echo "$RUNS" | python3 -c "
import sys, json
runs = json.load(sys.stdin)
for r in runs:
    if r.get('homework_id') == '${HW_ID}' and r.get('status') in ('succeeded', 'failed'):
        print(r.get('status'))
        steps = r.get('steps') or []
        nodes = [s.get('node') for s in steps]
        print('NODES:' + ','.join(nodes))
        break
" 2>/dev/null || true)
  if [ -n "$match" ]; then
    found="$match"
    break
  fi
  sleep 1
done

if [ -z "$found" ]; then
  echo "FAIL: no completed graph run for ${HW_ID} within ${MAX_WAIT}s"
  exit 1
fi

status=$(echo "$found" | head -1)
nodes=$(echo "$found" | grep NODES: | cut -d: -f2)
echo "Graph status: ${status}"
echo "Nodes: ${nodes}"

if [ "$status" != "succeeded" ]; then
  echo "FAIL: graph run not succeeded"
  exit 1
fi

for need in load_homework rag_validate eval_agent observer aplus_aggregate diagnosis planning finalize_run; do
  if ! echo "$nodes" | grep -q "$need"; then
    echo "FAIL: missing node $need"
    exit 1
  fi
done

echo "== eval_agent step present =="
echo "$RUNS" | python3 -c "
import sys, json
runs = json.load(sys.stdin)
for r in runs:
    if r.get('homework_id') == '${HW_ID}':
        for s in r.get('steps') or []:
            if s.get('node') == 'eval_agent':
                detail = s.get('detail', '')
                print('OK: eval_agent step:', detail)
                import re
                m = re.search(r'eval_updated=(\d+)', detail)
                updated = int(m.group(1)) if m else -1
                if updated == 0:
                    print('FAIL: eval_agent updated 0 questions (check vLLM / eval_v4_hints / thinking)')
                    sys.exit(1)
                if 'traces=' not in detail:
                    print('WARN: eval_agent detail missing traces= (M2a log summary)')
                break
        break
"

echo "== wrong-book after eval =="
curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/students/${STUDENT}/wrong-book?homework_id=${HW_ID}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for key in ('items', 'candidates', 'count', 'candidate_count'):
    assert key in d, f'missing {key}'
print('OK: wrong-book dual list', d['count'], 'confirmed,', d['candidate_count'], 'candidates')
"

curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/students/${STUDENT}/homework/${HW_ID}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
qs = d.get('questions') or []
assert qs, 'no questions'
q = qs[0]
for key in ('eval_json', 'eval_status', 'agent_is_wrong', 'wrong_source'):
    assert key in q, f'missing {key} on question'
ev = q.get('eval_json') or {}
if isinstance(ev, str):
    import json as _json
    ev = _json.loads(ev)
tr = ev.get('eval_trace') or {}
if tr.get('trace_version') == 1:
    print('OK: eval_trace trace_version=1 on homework question')
else:
    print('WARN: eval_trace missing on homework question (eval_debug_trace?)')
print('OK: homework question eval fields after graph')
"

echo "PASS: acceptance-graph-m3"
