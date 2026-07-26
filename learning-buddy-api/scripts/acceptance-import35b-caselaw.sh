#!/usr/bin/env bash
# v4 判例治理验收：T2 视觉比对 / 判例库 / 离线归纳晋级门。
# 全部纯代码路径（无 Milvus / 无 vLLM）——CI 可离线跑。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
API_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${API_ROOT}"

PY="${API_ROOT}/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  PY=python3
fi

echo "== 判例治理 golden：默认全关 + 记录 + 归纳晋级门 =="
if ! PYTHONPATH=. "${PY}" -m pytest --version >/dev/null 2>&1; then
  echo "SKIP: pytest not installed (run on host with .venv)"
else
  PYTHONPATH=. "${PY}" -m pytest tests/test_import_35b_caselaw.py -q
fi

echo "== 开关默认关闭自检 =="
PYTHONPATH=. "${PY}" -c "
from app.config import settings
assert settings.import35b_caselaw_enabled is False, '判例库默认必须关闭'
assert settings.import35b_agent_check_enabled is False, 'T2 默认必须关闭'
assert settings.import35b_caselaw_collection == 'import35b_caselaw', '判例 collection 必须独立'
assert settings.import35b_caselaw_collection != settings.milvus_collection, '判例 collection 不得与语料库同名'
print('OK: caselaw/agent 默认关闭 · collection 独立于 lb_corpus')
"

echo "== 晋级门 golden 全绿（当前基线）=="
PYTHONPATH=. "${PY}" -c "
from app.import_35b.caselaw.induction import golden_gate
g = golden_gate()
assert g.passed, g.golden_failures
print('OK: golden gate PASS', g.golden_total, '例')
"

echo "== 隔离性自检：判例向量层无 Milvus 时降级 no-op =="
PYTHONPATH=. "${PY}" -c "
from app.import_35b.caselaw import store as vs
# 强制模拟无 pymilvus
vs._pymilvus.cache_clear()
import types
vs._pymilvus = lambda: None
assert vs.available() is False
assert vs.search_cases('x', kind='misjudgment', subject='math', grade_band='hs') == []
print('OK: 无 Milvus 降级 no-op，绝不阻断')
"

echo "ALL PASS: 35B v4 判例治理验收通过"
