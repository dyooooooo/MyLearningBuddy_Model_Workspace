#!/usr/bin/env bash
# Golden regression for 35B import layout_blueprint_v4 (inline $...$ LaTeX harness).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MATH_DIR="${API_ROOT}/app/math"

echo "== 35B v4 golden: prompt version =="
cd "${API_ROOT}"
PY="${API_ROOT}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  PY=python3
fi
PYTHONPATH=. "${PY}" -c "
from app.import_35b.harness_golden import prompt_version_matches, load_spec
spec = load_spec()
assert prompt_version_matches(), 'PROMPT_VERSION must be layout_blueprint_v4'
print('OK: prompt_target=', spec['prompt_target'])
"

echo "== math bridge smoke (LaTeX validation gate) =="
if [ ! -d "${MATH_DIR}/node_modules/@plurimath/plurimath" ]; then
  echo "Installing math bridge deps..."
  cd "${MATH_DIR}"
  npm install --omit=dev --registry=https://registry.npmmirror.com
fi
echo '{"text":"x \\in \\mathbb{N}","format":"latex"}' | node "${MATH_DIR}/plurimath_bridge.mjs" | "${PY}" -c "
import json,sys
d=json.load(sys.stdin)
assert d.get('valid') is True, d
print('OK: latex gate valid=', d.get('valid'))
"

echo "== pytest 35b v4 golden + v3 structural regressions =="
cd "${API_ROOT}"
if ! PYTHONPATH=. "${PY}" -m pytest --version >/dev/null 2>&1; then
  echo "SKIP: pytest not installed (run on host with .venv for full golden regression)"
else
  PYTHONPATH=. "${PY}" -m pytest \
    tests/test_import_35b_schema_v4_golden.py \
    tests/test_import_35b_schema_v3.py \
    -q

  echo "== pytest math_segments golden (v4 dollar cases) = payload =="
  PYTHONPATH=. "${PY}" -m pytest tests/test_math_normalize.py -q
fi

echo "PASS: acceptance-import35b-v4"
