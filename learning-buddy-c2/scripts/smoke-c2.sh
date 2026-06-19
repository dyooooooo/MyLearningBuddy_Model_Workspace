#!/usr/bin/env bash
# C2 Gateway smoke test — health + page analyze
set -euo pipefail

C2_BASE="${C2_BASE_URL:-http://127.0.0.1:8000}"
SAMPLE="${1:-$(dirname "$0")/../fixtures/smoke_page.png}"

echo "=== C2 smoke: $C2_BASE ==="

echo "--- health ---"
curl -sf "$C2_BASE/api/v1/system/health" | python3 -m json.tool

if [[ -n "$SAMPLE" && -f "$SAMPLE" ]]; then
  echo "--- page analyze ---"
  curl -sf -X POST "$C2_BASE/api/v1/ocr/page" \
    -F "file=@$SAMPLE" | python3 -m json.tool | head -40
else
  echo "No sample image — skip OCR (pass path as arg 1)"
fi

echo "OK"
