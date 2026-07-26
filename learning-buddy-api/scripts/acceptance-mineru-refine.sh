#!/usr/bin/env bash
# MinerU refine dual-mode + page fusion + Edition Golden Master acceptance.
set -euo pipefail
WS="$(cd "$(dirname "$0")/.." && pwd)"
cd "$WS"

PYBIN="${WS}/.venv/bin/python3"
if [[ ! -x "$PYBIN" ]]; then
  PYBIN=python3
fi

echo "== mineru refine / page fusion unit tests =="
PYTHONPATH="$WS" "$PYBIN" -m pytest \
  tests/test_page_fusion_refine.py \
  tests/test_refine_block_id_coerce.py \
  -q

echo "== Edition Platform golden characterization (hujiao) =="
PYTHONPATH="$WS" "$PYBIN" -m pytest \
  tests/test_golden_hujiao_characterization.py \
  tests/test_editions_bundle.py \
  -q

echo "== golden fixtures present =="
test -f data/eval/mineru-content-correct-formula.json
test -f data/eval/mineru-layout-rebuild-header.json
test -f data/eval/textbook-layout-pages.json
test -d configs/editions/hujiao_math_hs_v1
test -f configs/editions/hujiao_math_hs_v1/config.json
test -f configs/editions/hujiao_math_hs_v1/policy.json
test -f configs/editions/hujiao_math_hs_v1/assets/theme.css
test -d configs/editions/_base
echo "OK"
