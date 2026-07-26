#!/usr/bin/env bash
# Verify Spark can use 127.0.0.1:7897 (SSH RemoteForward → Windows Clash).
set -euo pipefail

PROXY="${1:-http://127.0.0.1:7897}"
HOSTPORT="${PROXY#*://}"
HOSTPORT="${HOSTPORT%%/*}"
PORT="${HOSTPORT##*:}"

echo "== check listener :${PORT} =="
if ss -ltn | awk '{print $4}' | rg -q ":${PORT}$"; then
  echo "OK: something is listening on :${PORT}"
else
  echo "FAIL: nothing listening on :${PORT}"
  echo "Fix on Windows SSH config: RemoteForward ${PORT} 127.0.0.1:${PORT}"
  echo "Then reconnect Cursor Remote SSH, and keep Clash running."
  exit 1
fi

echo "== probe auth.kimi.com via proxy =="
code="$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 8 \
  -x "${PROXY}" https://auth.kimi.com/ || true)"
echo "HTTP ${code}"
if [[ "${code}" =~ ^[2345][0-9][0-9]$ ]]; then
  echo "OK: proxy path works (any HTTP status means TCP/TLS reached Kimi)."
  exit 0
fi
echo "FAIL: proxy could not reach auth.kimi.com"
exit 1
