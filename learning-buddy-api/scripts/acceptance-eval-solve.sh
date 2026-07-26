#!/usr/bin/env bash
set -euo pipefail

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
STUDENT="${STUDENT:-student-01}"
API_TOKEN="${API_TOKEN:-change-me-api}"
ADMIN_TOKEN="${ADMIN_TOKEN:-change-me-admin}"
MAX_WAIT="${MAX_WAIT:-180}"
LOG_DIR="${LOG_DIR:-$(cd "$(dirname "$0")/.." && pwd)/logs/eval}"

auth=(-H "Authorization: Bearer ${API_TOKEN}")
admin=(-H "Authorization: Bearer ${ADMIN_TOKEN}" -H "Content-Type: application/json")
admin_get=(-H "Authorization: Bearer ${ADMIN_TOKEN}")

echo "== M2a eval solve acceptance =="

echo "== Ensure pilot student ${STUDENT} =="
EXISTING=$(curl -sf "${admin_get[@]}" "${BUDDY_URL}/api/v1/admin/students" 2>/dev/null || echo "[]")
echo "$EXISTING" | python3 -c "import sys,json; ids=[s['id'] for s in json.load(sys.stdin)]; sys.exit(0 if '${STUDENT}' in ids else 1)" \
  || curl -sf -X POST "${BUDDY_URL}/api/v1/admin/students" "${admin[@]}" \
    -d "{\"id\":\"${STUDENT}\",\"label\":\"Eval M2a\",\"grade_band\":\"hs\"}" >/dev/null

echo "== GET /rag/eval-context solve_hints =="
CTX=$(curl -sf "${auth[@]}" \
  "${BUDDY_URL}/api/v1/rag/eval-context?q=$(python3 -c 'import urllib.parse; print(urllib.parse.quote("已知全集 U=(-∞,1)，集合 A=(-1,1)，则 A 的补集为"))')")
echo "$CTX" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert 'solve_hints' in d, 'missing solve_hints'
assert 'solve_hints_meta' in d, 'missing solve_hints_meta'
hints = d['solve_hints']
assert '不得抄写' in hints, 'hints missing constraint line'
assert 'questions' not in hints.lower() or '【' in hints
print('OK: solve_hints len=', len(hints))
"

HW_ID="eval-solve-$(date +%s)"
curl -sf -X POST "${BUDDY_URL}/api/v1/students/${STUDENT}/ingest/homework" \
  "${auth[@]}" -H "Content-Type: application/json" \
  -d "{
    \"id\": \"${HW_ID}\",
    \"session_id\": \"eval-session\",
    \"page_id\": \"p1\",
    \"finalized_at\": \"2026-06-20T10:00:00+00:00\",
    \"source_frame_ids\": [\"f1\"],
    \"parts\": [
      {\"kind\": \"question\", \"id\": \"1\", \"print_text\": \"已知全集 U=(-∞,1)，集合 A=(-1,1)，则 A 的补集为\", \"handwriting\": \"(-∞,1]\"},
      {\"kind\": \"question\", \"id\": \"2\", \"print_text\": \"解不等式 x²-5x+6>0\", \"handwriting\": \"x<2 或 x>3\"}
    ],
    \"meta\": {\"subject\": \"math\", \"grade_band\": \"hs\"}
  }" >/dev/null

echo "Ingested ${HW_ID}, triggering re-eval..."
curl -sf -X POST "${auth[@]}" \
  "${BUDDY_URL}/api/v1/students/${STUDENT}/homework/${HW_ID}/re-eval" >/dev/null

echo "Waiting for eval_trace (up to ${MAX_WAIT}s)..."
found=""
for i in $(seq 1 "${MAX_WAIT}"); do
  QUESTIONS=$(curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/students/${STUDENT}/homework/${HW_ID}")
  match=$(echo "$QUESTIONS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for q in data.get('questions') or []:
    ev = q.get('eval_json')
    if isinstance(ev, str):
        ev = json.loads(ev)
    tr = (ev or {}).get('eval_trace') or {}
    if tr.get('trace_version') == 1:
        print('OK')
        break
" 2>/dev/null || true)
  if [ -n "$match" ]; then
    found=1
    break
  fi
  sleep 1
done

if [ -z "$found" ]; then
  echo "FAIL: eval_trace not ready within ${MAX_WAIT}s"
  exit 1
fi

echo "$QUESTIONS" | python3 -c "
import sys, json
data = json.load(sys.stdin)
qs = data.get('questions') or []
assert qs, 'no questions'
trace_ok = 0
for q in qs:
    ev = q.get('eval_json')
    if isinstance(ev, str):
        ev = json.loads(ev)
    if not ev:
        continue
    tr = ev.get('eval_trace') or {}
    if tr.get('trace_version') == 1:
        trace_ok += 1
    stages = [s.get('stage') for s in (tr.get('stages') or [])]
    if 'solve_think' in stages and 'judge' in stages:
        print('OK: question', q.get('ordinal'), 'stages include solve_think+judge')
    if q.get('ordinal') == 1 or str(q.get('ordinal')) == '1':
        ref = ev.get('reference_answer') or ''
        if ref and '∪' not in ref and '{1}' not in ref:
            print('OK: complement reference', ref)
        if ev.get('is_wrong') is True:
            print('OK: complement wrong handwriting flagged')
assert trace_ok >= 1, 'no eval_trace trace_version=1'
print('OK: trace_version on', trace_ok, 'questions')
"

echo "== GET homework eval-traces =="
curl -sf "${auth[@]}" \
  "${BUDDY_URL}/api/v1/students/${STUDENT}/homework/${HW_ID}/eval-traces" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('homework_id'), 'missing homework_id'
assert d.get('trace_count', 0) >= 1, 'trace_count should be >= 1'
assert d.get('questions'), 'missing questions'
q0 = d['questions'][0]
assert 'eval_trace' in q0, 'missing eval_trace on question'
assert q0['eval_trace'].get('trace_version') == 1
print('OK: eval-traces trace_count=', d['trace_count'], 'avg_ms=', d.get('avg_ms'), 'log_file=', d.get('log_file'))
"

TODAY=$(date -u +%F)
JSONL="${LOG_DIR}/eval-${TODAY}.jsonl"
if [ -f "$JSONL" ]; then
  python3 -c "
import json, sys
path = sys.argv[1]
with open(path, encoding='utf-8') as f:
    lines = [ln for ln in f if ln.strip()]
assert lines, 'jsonl empty'
row = json.loads(lines[-1])
assert 'eval_trace' in row, 'jsonl row missing eval_trace'
print('OK: log file', path, 'lines=', len(lines))
" "$JSONL"
else
  echo "WARN: ${JSONL} not found (bind mount or re-eval path may differ)"
fi

echo "PASS: acceptance-eval-solve"
