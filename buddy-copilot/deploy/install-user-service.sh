#!/usr/bin/env bash
# 安装并启用 Buddy Copilot 为 systemd --user 服务（:8200）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/deploy/buddy-copilot.service"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/buddy-copilot.service"

mkdir -p "$(dirname "$UNIT_DST")"
mkdir -p "$ROOT/logs"
cp -f "$UNIT_SRC" "$UNIT_DST"

systemctl --user daemon-reload
systemctl --user enable buddy-copilot.service
systemctl --user restart buddy-copilot.service

echo "[buddy-copilot] user service installed: $UNIT_DST"
systemctl --user --no-pager --full status buddy-copilot.service || true

# 未开 Linger 时，注销后服务会停；尽量打开（需 sudo）
if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo no)" != "yes" ]]; then
  echo "[buddy-copilot] 提示：当前 Linger=no，注销后 :8200 会停。" >&2
  echo "  开机自启请执行：sudo loginctl enable-linger $USER" >&2
fi

echo "[buddy-copilot] health:"
sleep 1
curl -sS "http://127.0.0.1:8200/api/v1/copilot/health" || true
echo
