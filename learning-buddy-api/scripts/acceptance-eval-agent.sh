#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
STUDENT="${STUDENT:-student-01}"
API_TOKEN="${API_TOKEN:-change-me-api}"
CASES="${CASES:-${API_ROOT}/data/eval/eval_cases.jsonl}"

auth=(-H "Authorization: Bearer ${API_TOKEN}")

echo "== eval-agent golden =="

if [ ! -f "$CASES" ]; then
  echo "FAIL: missing $CASES"
  exit 1
fi

python3 <<PY
import json
from urllib.parse import quote
import urllib.request

cases = [json.loads(l) for l in open("${CASES}") if l.strip()]
url = "${BUDDY_URL}/api/v1/rag/eval-context?q=" + quote("解不等式 x²-5x+6>0")
req = urllib.request.Request(url, headers={"Authorization": "Bearer ${API_TOKEN}"})
with urllib.request.urlopen(req, timeout=30) as r:
    ctx = json.load(r)
assert ctx.get("kps") is not None or ctx.get("hits"), "eval-context missing hits/kps"
print(f"OK: eval-context keys={list(ctx.keys())[:8]}")
print(f"OK: {len(cases)} golden cases loaded")
PY

echo "== graph eval-context sample =="
curl -sf "${auth[@]}" "${BUDDY_URL}/api/v1/rag/eval-context?q=$(
  python3 -c 'import urllib.parse; print(urllib.parse.quote("分式不等式 (3x-1)/(x+1) > 0"))'
)" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'methods' in d or 'hits' in d; print('OK: methods=', len(d.get('methods',[])), 'shortcuts=', len(d.get('shortcuts',[])))"

echo "PASS: acceptance-eval-agent"
