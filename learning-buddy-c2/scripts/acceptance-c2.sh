#!/usr/bin/env bash
# Spark C2 acceptance — run on Spark after vLLM + gateway are up
set -euo pipefail

C2_BASE="${C2_BASE_URL:-http://127.0.0.1:8000}"
FIXTURE="$(dirname "$0")/../fixtures/smoke_page.png"

echo "=== Spark C2 acceptance ==="
echo "C2_BASE=$C2_BASE"

echo "--- 1. health ---"
curl -sf "$C2_BASE/api/v1/system/health" | python3 -c "
import sys, json
h = json.load(sys.stdin)
assert h.get('status') == 'ok', h
assert h.get('vllm_reachable') is True, 'vllm not reachable'
print('health OK')
"

echo "--- 2. page analyze ---"
curl -sf -X POST "$C2_BASE/api/v1/ocr/page" \
  -F "file=@$FIXTURE" --max-time 120 | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('task') == 'page_analyze', d
parts = d.get('parts', [])
assert len(parts) >= 1, 'no parts'
questions = [p for p in parts if p.get('kind') == 'question']
assert questions and questions[0].get('print'), questions[0]
print('page OK:', len(parts), 'parts,', len(questions), 'questions')
"

echo "--- 3. ports (no :8765) ---"
if ss -ltn | grep -q ':8765'; then
  echo "FAIL: :8765 still listening"
  exit 1
fi
echo "no :8765 OK"

echo "=== ACCEPTANCE PASSED ==="
