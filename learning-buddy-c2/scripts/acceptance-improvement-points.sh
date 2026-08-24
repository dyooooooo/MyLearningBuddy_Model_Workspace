#!/usr/bin/env bash
# Hard acceptance for Improvement Points main chain (daily) + migration residue checks.
set -euo pipefail

BASE="${BUDDY_API_BASE:-http://127.0.0.1:8100}"
TOKEN="${ADMIN_TOKEN:-${API_TOKEN:-}}"
BOOK2_JOB="${BOOK2_JOB_ID:-821d05bc-cd64-4a6c-8a0f-e51763b7ec60}"
BOOK2_FP="${BOOK2_FP:-038e8a194cc3b39d5bfa60a39972e7796acdc3af}"
# After Book2 labour backfill (page-level accepted IPs); override if needed.
BOOK2_ACCEPTED_MIN="${BOOK2_ACCEPTED_MIN:-40}"

if [[ -z "$TOKEN" ]]; then
  # shellcheck disable=SC1091
  source "$(dirname "$0")/../.env" 2>/dev/null || true
  TOKEN="${ADMIN_TOKEN:-${API_TOKEN:-}}"
fi
if [[ -z "$TOKEN" ]]; then
  echo "ADMIN_TOKEN/API_TOKEN required" >&2
  exit 1
fi

auth=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

fail=0
check() {
  local name="$1"
  shift
  if "$@"; then
    echo "PASS $name"
  else
    echo "FAIL $name" >&2
    fail=1
  fi
}

# 1) Book2 platform non-empty (not only smoke)
body=$(curl -sf "${auth[@]}" \
  "$BASE/api/v1/admin/corpus/improvement-points?fingerprint_hash=$BOOK2_FP")
accepted=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(int((d.get('counts') or {}).get('accepted') or 0))" <<<"$body")
check "book2 accepted IP >= $BOOK2_ACCEPTED_MIN (got $accepted)" test "$accepted" -ge "$BOOK2_ACCEPTED_MIN"

body_job=$(curl -sf "${auth[@]}" \
  "$BASE/api/v1/admin/corpus/improvement-points?job_id=$BOOK2_JOB")
accepted_job=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(int((d.get('counts') or {}).get('accepted') or 0))" <<<"$body_job")
check "book2 job accepted IP >= $BOOK2_ACCEPTED_MIN (got $accepted_job)" test "$accepted_job" -ge "$BOOK2_ACCEPTED_MIN"

# 2) Bind exists
bind=$(curl -sf "${auth[@]}" \
  "$BASE/api/v1/admin/corpus/books/$BOOK2_FP/bind-edition" || echo '{}')
eid=$(python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('edition_id') or '')" <<<"$bind")
check "book2 edition bind non-empty" test -n "$eid"

# 3) Linked corrections for book2 fp
linked=$(docker exec lb-postgres psql -U buddy -d buddy -t -A -c \
  "SELECT count(*) FROM corpus_corrections WHERE fingerprint_hash='$BOOK2_FP' AND coalesce(ip_id,'')<>'';" \
  | tr -d '[:space:]')
check "book2 corrections linked to IP (got $linked)" test "${linked:-0}" -gt 0

# 4) Confirm API still requires procedure
code=$(curl -s -o /tmp/ip_bad.json -w "%{http_code}" "${auth[@]}" \
  -X POST "$BASE/api/v1/admin/corpus/improvement-points/confirm" \
  -d '{"job_id":"accept-probe","page_no":1,"summary":"no procedure","attempts":[],"procedure":{"steps":[]},"write_correction":false}')
check "confirm without procedure → 400 (got $code)" test "$code" = "400"

# 5) UI-equivalent confirm (attempts present → accepted; origin closed set)
probe_job="accept-ui-probe-$(date +%s)"
code_ok=$(curl -s -o /tmp/ip_ui_ok.json -w "%{http_code}" "${auth[@]}" \
  -X POST "$BASE/api/v1/admin/corpus/improvement-points/confirm" \
  -d "{\"job_id\":\"$probe_job\",\"session_id\":\"accept-ui\",\"fingerprint_hash\":\"accept-ui-fp\",\"page_no\":99,\"summary\":\"UI dual-write probe\",\"authority\":\"edition\",\"kind\":\"cursor_ir\",\"origin\":\"user_ask\",\"attempts\":[{\"id\":\"ui.probe\",\"tool\":\"cursor_ir\",\"op\":\"cursor_ir\",\"page\":99,\"page_no\":99,\"summary\":\"UI dual-write probe\",\"ok\":true,\"at\":\"2026-07-30T00:00:00Z\"}],\"pattern_status\":\"none\",\"write_correction\":false}")
st=$(python3 -c "import json; d=json.load(open('/tmp/ip_ui_ok.json')); print((d.get('ip') or {}).get('status') or '')" 2>/dev/null || echo "")
origin=$(python3 -c "import json; d=json.load(open('/tmp/ip_ui_ok.json')); print((d.get('ip') or {}).get('origin') or '')" 2>/dev/null || echo "")
ps=$(python3 -c "import json; d=json.load(open('/tmp/ip_ui_ok.json')); print((d.get('ip') or {}).get('pattern_status') or '')" 2>/dev/null || echo "")
check "UI-equivalent confirm → 200 (got $code_ok)" test "$code_ok" = "200"
check "UI-equivalent confirm → accepted (got $st)" test "$st" = "accepted"
check "UI-equivalent confirm → origin user_ask (got $origin)" test "$origin" = "user_ask"
check "UI-equivalent confirm → pattern_status none (got $ps)" test "$ps" = "none"

# 6) Book2 inventory: origins only ai_auto|user_ask (post-cleanup invariant)
bad_origin=$(docker exec lb-postgres psql -U buddy -d buddy -t -A -c \
  "SELECT count(*) FROM corpus_improvement_points WHERE fingerprint_hash='$BOOK2_FP' AND coalesce(origin,'') NOT IN ('ai_auto','user_ask');" \
  | tr -d '[:space:]')
check "book2 origins closed set (bad=$bad_origin)" test "${bad_origin:-0}" = "0"

if [[ "$fail" -ne 0 ]]; then
  echo "acceptance-improvement-points FAILED" >&2
  exit 1
fi
echo "acceptance-improvement-points OK"
exit 0
