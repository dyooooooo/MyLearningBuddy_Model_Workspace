#!/usr/bin/env bash
# Corpus workqueue acceptance smoke
set -euo pipefail

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
ADMIN_TOKEN="${ADMIN_TOKEN:-change-me-admin}"
admin=(-H "Authorization: Bearer ${ADMIN_TOKEN}")

echo "== GET /workqueue =="
curl -sf "${admin[@]}" "${BUDDY_URL}/api/v1/admin/corpus/workqueue?limit=10" | python3 -m json.tool

echo "PASS acceptance-corpus-workqueue.sh"
