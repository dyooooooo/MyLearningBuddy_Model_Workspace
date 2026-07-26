#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MATH_DIR="${API_ROOT}/app/math"

echo "== math bridge smoke =="
if [ ! -d "${MATH_DIR}/node_modules/@plurimath/plurimath" ]; then
  echo "Installing math bridge deps..."
  cd "${MATH_DIR}"
  npm install --omit=dev --registry=https://registry.npmmirror.com
fi

echo '{"text":"x_1 != x_2","format":"asciimath"}' | node "${MATH_DIR}/plurimath_bridge.mjs" | python3 -c "
import json,sys
d=json.load(sys.stdin)
assert d.get('valid') is True, d
print('OK: bridge valid=', d.get('valid'))
"

echo "== pytest math_normalize =="
cd "${API_ROOT}"
PY="${API_ROOT}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  PY=python3
fi
PYTHONPATH=. "${PY}" -m pytest tests/test_math_normalize.py -q

echo "== frontend segment renderer =="
cd "${API_ROOT}/../exam-ocr-server/frontend"
node scripts/test-math.mjs

echo "PASS: acceptance-math-segments"
