#!/usr/bin/env bash
# PDF-import TOC leaf + KB boundary + RAG hybrid smoke
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

echo "== Unit: corpus tree toc_leaf + deepen + rrf =="
"$PY" -m pytest \
  tests/test_corpus_tree_from_ir.py \
  tests/test_textbook_toc_deepen.py \
  tests/test_rrf.py \
  -q

echo "== Boundary: textbook deepen NOT imported by publish/rag =="
"$PY" - <<'PY'
from pathlib import Path
root = Path("app")
forbidden_importers = [
    root / "services" / "corpus_publish.py",
    root / "rag" / "retriever.py",
    root / "rag" / "indexer.py",
]
needle = "textbook_toc_deepen"
for p in forbidden_importers:
    text = p.read_text(encoding="utf-8")
    assert needle not in text, f"{p} must not reference {needle}"
print("OK: deepen confined to import domain")
PY

echo "== Boundary: import path references deepen =="
grep -q "textbook_toc_deepen" app/services/corpus_import_incremental.py
grep -q "toc_leaf" app/services/corpus_tree_from_ir.py

BUDDY_URL="${BUDDY_URL:-http://127.0.0.1:8100}"
API_TOKEN="${API_TOKEN:-change-me-api}"
if curl -sf -o /dev/null -H "Authorization: Bearer ${API_TOKEN}" "${BUDDY_URL}/api/v1/system/health"; then
  echo "== Live RAG search smoke =="
  curl -sf -H "Authorization: Bearer ${API_TOKEN}" \
    --get "${BUDDY_URL}/api/v1/rag/search" \
    --data-urlencode "q=集合" \
    --data-urlencode "subject=math" \
    --data-urlencode "grade_band=hs" \
    | "$PY" -c "import json,sys; d=json.load(sys.stdin); assert isinstance(d,list); print('rag hits:', len(d))"
else
  echo "SKIP live RAG (buddy-api not up at ${BUDDY_URL})"
fi

echo "PASS acceptance-toc-structure-rag.sh"
