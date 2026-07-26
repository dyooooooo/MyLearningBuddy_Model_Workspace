#!/usr/bin/env bash
# Buddy Copilot 宿主机编排服务启动脚本
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -d .venv ]]; then
  echo "[buddy-copilot] .venv 不存在，先创建虚拟环境：" >&2
  echo "  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "[buddy-copilot] 警告：缺少 .env（可从 .env.example 复制）；将只用环境变量与默认值" >&2
fi

HOST="${COPILOT_HOST:-127.0.0.1}"
PORT="${COPILOT_PORT:-8200}"

exec ./.venv/bin/uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
