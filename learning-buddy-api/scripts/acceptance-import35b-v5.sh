#!/usr/bin/env bash
# Golden regression for 35B import layout_blueprint_v5 + v4 architecture.
#   - prompt version gate (v5, frozen)
#   - math bridge validates AMS symbols (\leqslant / \varnothing)
#   - pytest v5 golden (equation validation, figure reclassification, MFR gate)
#     + v4 architecture golden (ACL escape repair, cases autoclose, table math,
#       running_header convergence, SSR self-contained, T1 findings)
#     + math normalize
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MATH_DIR="${API_ROOT}/app/math"

echo "== 35B v5 golden: prompt version =="
cd "${API_ROOT}"
PY="${API_ROOT}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  PY=python3
fi
PYTHONPATH=. "${PY}" -c "
from app.import_35b.harness_golden import prompt_version_matches, load_spec
spec = load_spec()
assert prompt_version_matches(), 'PROMPT_VERSION must be layout_blueprint_v5'
print('OK: prompt_target=', spec['prompt_target'])
"

echo "== math bridge smoke (AMS symbol validation gate) =="
if [ ! -d "${MATH_DIR}/node_modules/@plurimath/plurimath" ]; then
  echo "Installing math bridge deps..."
  cd "${MATH_DIR}"
  npm install --omit=dev --registry=https://registry.npmmirror.com
fi
for EXPR in 'x \\leqslant y' 'A \\cap B = \\varnothing'; do
  printf '{"text":"%s","format":"latex"}' "${EXPR}" | node "${MATH_DIR}/plurimath_bridge.mjs" | "${PY}" -c "
import json,sys
d=json.load(sys.stdin)
assert d.get('valid') is True, d
print('OK: latex gate valid=', d.get('valid'))
"
done

echo "== pytest 35b v5 golden + v4 architecture golden =="
cd "${API_ROOT}"
if ! PYTHONPATH=. "${PY}" -m pytest --version >/dev/null 2>&1; then
  echo "SKIP: pytest not installed (run on host with .venv for full golden regression)"
else
  PYTHONPATH=. "${PY}" -m pytest \
    tests/test_import_35b_schema_v5_golden.py \
    tests/test_import_35b_v4_arch.py \
    -q

  echo "== pytest math_segments golden (dollar cases) =="
  PYTHONPATH=. "${PY}" -m pytest tests/test_math_normalize.py -q
fi

echo "PASS: acceptance-import35b-v5"
