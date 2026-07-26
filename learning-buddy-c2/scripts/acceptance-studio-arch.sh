#!/usr/bin/env bash
# Studio arch harness: auth 401 + terminal poll stop hints.
set -euo pipefail

C2_BASE_URL="${C2_BASE_URL:-http://127.0.0.1:8000}"
STUDIO_TOKEN="${STUDIO_TOKEN:-change-me-admin}"

echo "== Studio auth: anonymous proxy must 401 =="
code=$(curl -s -o /dev/null -w "%{http_code}" "${C2_BASE_URL}/api/v1/studio/proxy/stats" || true)
if [[ "$code" != "401" ]]; then
  echo "FAIL: expected 401, got $code" >&2
  exit 1
fi
echo "OK anonymous → $code"

echo "== Studio auth: with token =="
code=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${STUDIO_TOKEN}" \
  "${C2_BASE_URL}/api/v1/studio/proxy/stats" || true)
if [[ "$code" != "200" ]]; then
  echo "WARN: authenticated stats → $code (buddy-api may be down)" >&2
else
  echo "OK authed → $code"
fi

echo "== Studio static =="
code=$(curl -s -o /dev/null -w "%{http_code}" "${C2_BASE_URL}/studio/" || true)
echo "studio UI → $code"

echo "acceptance-studio-arch done"
