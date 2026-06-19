#!/usr/bin/env bash
# System-level mirror config (requires sudo). Run after setup-china-mirrors.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APT_SOURCES="/etc/apt/sources.list.d/ubuntu.sources"

echo "=== DGX Spark China Mirrors — system (sudo) ==="

if [[ -f "$APT_SOURCES" ]] && grep -q 'ports.ubuntu.com/ubuntu-ports' "$APT_SOURCES"; then
  sudo sed -i 's|http://ports.ubuntu.com/ubuntu-ports/|https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/|g' "$APT_SOURCES"
  echo "OK: apt → Tsinghua ubuntu-ports"
  sudo apt-get update -qq
else
  echo "OK: apt mirror already set or sources file missing"
fi

sudo cp "$SCRIPT_DIR/configs/docker-daemon.json" /etc/docker/daemon.json
echo "OK: /etc/docker/daemon.json"

sudo systemctl daemon-reload
sudo systemctl restart docker
echo "OK: docker restarted"

echo ""
echo "System mirrors applied. Run: tools/check-dgx-health.sh"
