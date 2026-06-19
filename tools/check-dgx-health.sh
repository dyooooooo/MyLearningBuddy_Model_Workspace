#!/usr/bin/env bash
set -euo pipefail

[[ -f "$HOME/.bashrc.d/china-mirrors.sh" ]] && source "$HOME/.bashrc.d/china-mirrors.sh"

docker_cmd() {
  local has_docker_group=false
  getent group docker 2>/dev/null | grep -qE "[,:]${USER}([,]|$)" && has_docker_group=true
  if docker info &>/dev/null; then
    docker "$@"
  elif $has_docker_group && command -v sg &>/dev/null; then
    sg docker -c "docker $*"
  else
    return 1
  fi
}

echo "=== DGX Spark Health Check ==="
echo ""

echo "--- System ---"
hostname
cat /etc/dgx-release 2>/dev/null | grep -E 'DGX_|PLATFORM|SERIAL' || true
uptime
echo ""

echo "--- GPU ---"
nvidia-smi --query-gpu=name,driver_version,temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null || echo "nvidia-smi failed"
echo ""

echo "--- Docker ---"
systemctl is-active docker 2>/dev/null || echo "docker service unknown"
if docker_cmd ps 2>/dev/null | head -5; then
  :
else
  echo "docker ps failed (run: newgrp docker  — or open a new login shell)"
fi
echo ""

echo "--- Dashboard ---"
code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 http://localhost:11000/ 2>/dev/null || echo "000")
echo "Dashboard http://localhost:11000 → HTTP $code"
echo ""

echo "--- Disk / Memory ---"
df -h / | tail -1
free -h | grep -E '^Mem|^Swap' || true
echo ""

echo "--- Mirrors / registries ---"
if docker_cmd info 2>/dev/null | grep -A5 "Registry Mirrors"; then
  :
elif [[ -f /etc/docker/daemon.json ]] && grep -q 'registry-mirrors' /etc/docker/daemon.json 2>/dev/null; then
  echo "Docker mirrors (from /etc/docker/daemon.json):"
  grep -A10 'registry-mirrors' /etc/docker/daemon.json | grep 'https://' | sed 's/[",]//g' | sed 's/^/  /'
else
  echo "Docker mirrors: not configured"
fi
echo "HF_ENDPOINT=${HF_ENDPOINT:-not set}"
npm config get registry 2>/dev/null || true
pip config get global.index-url 2>/dev/null || echo "pip index: not set"
for url in \
  "https://nvcr.io/v2/" \
  "https://hf-mirror.com" \
  "https://pypi.tuna.tsinghua.edu.cn/simple" \
  "https://registry.npmmirror.com" \
  "https://docker.m.daocloud.io" \
  "https://mcp.context7.com/mcp"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url" 2>/dev/null || echo "timeout")
  echo "$url → $code"
done
echo ""
echo "Done."
