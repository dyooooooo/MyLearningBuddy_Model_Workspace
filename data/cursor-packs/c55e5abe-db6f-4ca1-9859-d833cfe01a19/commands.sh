#!/usr/bin/env bash
# Cursor Repair Pack · c55e5abe-db6f-4ca1-9859-d833cfe01a19
set -euo pipefail
PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_ROOT="$(cd "$PACK_DIR/../../learning-buddy-api" 2>/dev/null && pwd || echo "")"

reproduce() {
  if [[ -z "$API_ROOT" || ! -d "$API_ROOT" ]]; then
    echo "learning-buddy-api not found relative to pack; open evidence/ manually"
    return 1
  fi
  cd "$API_ROOT"
  PYTHONPATH=. python3 scripts/replay-import35b.py --dir "$PACK_DIR/evidence" --json /tmp/replay-c55e5abe-db6f-4ca1-9859-d833cfe01a19.json
}

acceptance() {
  if [[ -z "$API_ROOT" || ! -d "$API_ROOT" ]]; then
    echo "skip acceptance — API root missing"
    return 0
  fi
  cd "$API_ROOT"
  if [[ -x scripts/acceptance-import35b-v5.sh ]]; then
    bash scripts/acceptance-import35b-v5.sh
  else
    PYTHONPATH=. python3 -m pytest tests/test_import_35b_v4_arch.py -q
  fi
}

case "${1:-help}" in
  reproduce) reproduce ;;
  acceptance) acceptance ;;
  *) echo "Usage: $0 reproduce|acceptance" ;;
esac
