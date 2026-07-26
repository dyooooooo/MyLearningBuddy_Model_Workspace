#!/usr/bin/env bash
# 清空 Milvus + PG 语料图（完全空库，默认不载入任何种子数据）
set -euo pipefail

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
ADMIN_TOKEN="${ADMIN_TOKEN:-change-me-admin}"
admin=(-H "Authorization: Bearer ${ADMIN_TOKEN}")

echo "== Reset corpus workspace (empty) =="

curl -sf -X POST "${admin[@]}" \
  "${BUDDY_URL}/api/v1/admin/corpus/reset-workspace?reseed=false" | python3 -m json.tool

echo "== Stats after reset =="
curl -sf "${admin[@]}" "${BUDDY_URL}/api/v1/admin/corpus/stats" | python3 -m json.tool

echo "PASS reset-corpus-governed.sh"
