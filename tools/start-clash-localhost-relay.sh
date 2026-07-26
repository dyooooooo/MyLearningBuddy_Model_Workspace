#!/usr/bin/env bash
# Start 127.0.0.1:7897 -> Windows Clash relay for Cursor Remote / Kimi.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ROOT}/tools/clash-localhost-relay.py"
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/clash-localhost-relay.pid"
LOGFILE="${XDG_RUNTIME_DIR:-/tmp}/clash-localhost-relay.log"

detect_clash_host() {
  if [[ -n "${CLASH_HOST:-}" ]]; then
    echo "${CLASH_HOST}"
    return
  fi
  # Prefer current SSH client (Windows Cursor / SSH)
  if [[ -n "${SSH_CLIENT:-}" ]]; then
    echo "${SSH_CLIENT%% *}"
    return
  fi
  # Fallback: latest established SSH peer
  local peer
  peer="$(ss -tn state established '( sport = :22 )' 2>/dev/null \
    | awk 'NR>1 {print $4}' | head -1 | cut -d: -f1 || true)"
  if [[ -n "${peer}" ]]; then
    echo "${peer}"
    return
  fi
  echo "192.168.1.7"
}

CLASH_HOST="$(detect_clash_host)"
CLASH_PORT="${CLASH_PORT:-7897}"
export CLASH_HOST CLASH_PORT

echo "== target Clash ${CLASH_HOST}:${CLASH_PORT} =="
if ! timeout 3 bash -c "echo >/dev/tcp/${CLASH_HOST}/${CLASH_PORT}" 2>/dev/null; then
  echo "FAIL: cannot reach ${CLASH_HOST}:${CLASH_PORT}" >&2
  echo "On Windows: start Clash and enable Allow LAN." >&2
  exit 1
fi

if [[ -f "${PIDFILE}" ]]; then
  oldpid="$(cat "${PIDFILE}" || true)"
  if [[ -n "${oldpid}" ]] && kill -0 "${oldpid}" 2>/dev/null; then
    echo "stopping old relay pid=${oldpid}"
    kill "${oldpid}" 2>/dev/null || true
    sleep 0.3
  fi
  rm -f "${PIDFILE}"
fi

# Free port if a stale process holds it
if ss -ltn | awk '{print $4}' | rg -q ':7897$'; then
  echo "port 7897 already in use; not starting another relay" >&2
  ss -ltnp | rg 7897 || true
  exit 1
fi

nohup python3 "${PY}" --target-host "${CLASH_HOST}" --target-port "${CLASH_PORT}" \
  >"${LOGFILE}" 2>&1 &
echo $! >"${PIDFILE}"
sleep 0.4

if ! kill -0 "$(cat "${PIDFILE}")" 2>/dev/null; then
  echo "FAIL: relay exited; see ${LOGFILE}" >&2
  tail -n 20 "${LOGFILE}" >&2 || true
  exit 1
fi

echo "OK: relay pid=$(cat "${PIDFILE}") log=${LOGFILE}"
ss -ltn | rg 7897 || true
"${ROOT}/tools/check-kimi-proxy.sh" || true
