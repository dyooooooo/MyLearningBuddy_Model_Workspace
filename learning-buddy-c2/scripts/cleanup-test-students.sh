#!/usr/bin/env bash
# Remove acceptance / scratch students (keeps student-01). Requires buddy-api + admin token.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
source "${ROOT}/.env"
BUDDY_URL="${BUDDY_URL:-http://localhost:8100}"
ADMIN_TOKEN="${ADMIN_TOKEN:-change-me-admin}"
KEEP="${KEEP:-student-01}"
export KEEP

to_delete="$(curl -sf "${BUDDY_URL}/api/v1/admin/students" -H "Authorization: Bearer ${ADMIN_TOKEN}" | python3 -c "
import json, os, sys
keep = os.environ.get('KEEP', 'student-01')
for r in json.load(sys.stdin):
    sid = r.get('id', '')
    if not sid or sid == keep:
        continue
    if sid.startswith('m1-accept-') or sid.startswith('accept-'):
        print(sid)
")"

if [[ -z "${to_delete}" ]]; then
  echo "No test students to delete."
else
  while read -r sid; do
    [[ -z "${sid}" ]] && continue
    echo "DELETE ${sid}"
    curl -sf -X DELETE "${BUDDY_URL}/api/v1/admin/students/${sid}" \
      -H "Authorization: Bearer ${ADMIN_TOKEN}"
    echo
  done <<< "${to_delete}"
fi

echo "Remaining:"
curl -sf "${BUDDY_URL}/api/v1/admin/students" -H "Authorization: Bearer ${ADMIN_TOKEN}" | python3 -m json.tool
