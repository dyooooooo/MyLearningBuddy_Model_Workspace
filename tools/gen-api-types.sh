#!/usr/bin/env bash
# Generate TypeScript types from Buddy + Gateway OpenAPI (services must be up).
# Primary consumer: studio-ui. Also writes to frontend/ for the /test harness.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUDIO="${ROOT}/exam-ocr-server/studio-ui"
FE="${ROOT}/exam-ocr-server/frontend"
BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8000}"

if ! command -v npx >/dev/null 2>&1; then
  echo "npx required; install Node.js 20+" >&2
  exit 1
fi

gen_into() {
  local dir="$1"
  mkdir -p "${dir}/src/api/generated"
  cd "${dir}"
  npm install --no-save openapi-typescript@7 2>/dev/null || npm install --no-save openapi-typescript@7
  npx openapi-typescript "${BUDDY_URL}/openapi.json" -o src/api/generated/buddy.ts
  npx openapi-typescript "${GATEWAY_URL}/openapi.json" -o src/api/generated/gateway.ts
  echo "Generated under ${dir}/src/api/generated/"
}

# Studio is the primary typed client.
gen_into "${STUDIO}"

# Keep /test frontend types in sync when that tree exists.
if [[ -d "${FE}" ]]; then
  gen_into "${FE}" || echo "frontend generate skipped/failed" >&2
fi
