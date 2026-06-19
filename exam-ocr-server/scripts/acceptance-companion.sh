#!/usr/bin/env bash
# Companion pipeline API acceptance (no full OCR unless COMPANION_TEST_IMAGE set).
set -euo pipefail

BASE="${COMPANION_BASE_URL:-http://127.0.0.1:8000}"
API="${BASE}/api/v1/companion"

pass=0
fail=0

ok() { echo "  OK  $*"; pass=$((pass + 1)); }
bad() { echo "  FAIL $*"; fail=$((fail + 1)); }

echo "=== Companion API acceptance @ ${BASE} ==="

# 1. Health
if curl -sf "${BASE}/api/v1/system/health" >/dev/null; then
  ok "gateway health"
else
  bad "gateway health"
  echo "Gateway not reachable; aborting."
  exit 1
fi

# 2. Create session
SESSION_JSON=$(curl -sf -X POST "${API}/sessions" -H "Content-Type: application/json" -d '{}')
SESSION_ID=$(echo "$SESSION_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['session']['id'])")
if [[ -n "$SESSION_ID" ]]; then
  ok "POST /sessions -> ${SESSION_ID:0:8}…"
else
  bad "POST /sessions"
  exit 1
fi

# 3. GET session
if curl -sf "${API}/sessions/${SESSION_ID}" | grep -q '"status":"active"'; then
  ok "GET /sessions/{id}"
else
  bad "GET /sessions/{id}"
fi

# 4. Homework list (empty ok)
if curl -sf "${API}/homework" | grep -q '"entries"'; then
  ok "GET /homework"
else
  bad "GET /homework"
fi

# 5. Mastery list
if curl -sf "${API}/mastery" | grep -q '"demo":true'; then
  ok "GET /mastery (demo)"
else
  bad "GET /mastery"
fi

# 6. Export empty session
if curl -sf "${API}/sessions/${SESSION_ID}/export" | grep -q '"session"'; then
  ok "GET /sessions/{id}/export"
else
  bad "GET /sessions/{id}/export"
fi

# 7. Optional: add frame with test image (requires vLLM)
TEST_IMAGE="${COMPANION_TEST_IMAGE:-}"
if [[ -n "$TEST_IMAGE" && -f "$TEST_IMAGE" ]]; then
  echo "  … add frame with ${TEST_IMAGE}"
  FRAME_JSON=$(curl -sf -X POST "${API}/sessions/${SESSION_ID}/frames" -F "file=@${TEST_IMAGE}")
  if echo "$FRAME_JSON" | grep -q '"frame_id"'; then
    ok "POST /sessions/{id}/frames"
    ACTION=$(echo "$FRAME_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('scheduler_action',''))")
    echo "       scheduler_action=${ACTION}"
  else
    bad "POST /sessions/{id}/frames"
  fi
  # finalize
  if curl -sf -X POST "${API}/sessions/${SESSION_ID}/finalize-page" | grep -q '"session"'; then
    ok "POST /sessions/{id}/finalize-page"
  else
    bad "finalize-page"
  fi
else
  echo "  SKIP frame OCR (set COMPANION_TEST_IMAGE=/path/to.jpg to test)"
fi

# 8. End session
if curl -sf -X POST "${API}/sessions/${SESSION_ID}/end" | grep -q '"session"'; then
  ok "POST /sessions/{id}/end"
else
  bad "POST /sessions/{id}/end"
fi

echo ""
echo "Result: ${pass} passed, ${fail} failed"
[[ "$fail" -eq 0 ]]
