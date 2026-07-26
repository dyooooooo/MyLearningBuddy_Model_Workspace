#!/usr/bin/env bash
# Corpus Studio tree/navigation API — read-only smoke (no seed nodes)
set -euo pipefail

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
ADMIN_TOKEN="${ADMIN_TOKEN:-change-me-admin}"
admin=(-H "Authorization: Bearer ${ADMIN_TOKEN}")

echo "== Tree textbook roots =="
curl -sf "${admin[@]}" "${BUDDY_URL}/api/v1/admin/corpus/tree?root=textbook&subject=math&grade_band=hs" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert isinstance(d,list); print('textbook roots:', len(d))"

echo "== Tree kp roots =="
curl -sf "${admin[@]}" "${BUDDY_URL}/api/v1/admin/corpus/tree?root=kp&subject=math&grade_band=hs" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('kp roots:', len(d))"

echo "== Node search =="
curl -sf "${admin[@]}" --get "${BUDDY_URL}/api/v1/admin/corpus/nodes/search" \
  --data-urlencode "q=集合" \
  --data-urlencode "limit=5" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('search hits:', len(d))"

echo "== Stats (expect empty KB until manual publish) =="
curl -sf "${admin[@]}" "${BUDDY_URL}/api/v1/admin/corpus/stats" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'milvus_schema_ok' in d; print('nodes:', d.get('node_count'), 'milvus:', d.get('milvus_count'))"

echo "PASS acceptance-corpus-tree.sh"
