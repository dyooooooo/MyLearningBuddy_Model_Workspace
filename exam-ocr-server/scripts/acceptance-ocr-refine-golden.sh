#!/usr/bin/env bash
# Golden tests for print-only ocr_refine (mock vLLM, no GPU).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WS="$(cd "${ROOT}/.." && pwd)"
CASES="${WS}/learning-buddy-api/data/eval/ocr_refine_cases.jsonl"
if [[ ! -f "${CASES}" ]]; then
  CASES="${ROOT}/tests/fixtures/ocr_refine_cases.jsonl"
fi
if [[ ! -f "${CASES}" ]]; then
  CASES="${ROOT}/scripts/fixtures/ocr_refine_cases.jsonl"
fi
export PYTHONPATH="${ROOT}"
cd "${ROOT}"

if [[ ! -f "${CASES}" ]]; then
  echo "FAIL: missing ${CASES}"
  exit 1
fi

CASES="${CASES}" python3 << 'PY'
import json
import os
import sys
from pathlib import Path

from app.ocr_refine import _raw_to_parts
from app.schemas import PagePart

cases_path = Path(os.environ["CASES"])
failed = 0
passed = 0

for line in cases_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    case = json.loads(line)
    cid = case["id"]
    draft = [PagePart.model_validate(p) for p in case["vision_parts"]]
    refined = _raw_to_parts(case["mock_refine_response"], draft)
    ok = True

    if case.get("handwriting_must_equal_vision"):
        draft_q = {p.id: p for p in draft if p.kind == "question"}
        refined_q = {p.id: p for p in refined if p.kind == "question"}
        for qid, d in draft_q.items():
            r = refined_q.get(qid)
            if r is None:
                print(f"FAIL {cid}: missing question {qid} in refined output")
                failed += 1
                ok = False
                break
            if (d.handwriting or "") != (r.handwriting or ""):
                print(
                    f"FAIL {cid}: handwriting changed Q{qid}: "
                    f"{d.handwriting!r} -> {r.handwriting!r}"
                )
                failed += 1
                ok = False
                break

    if ok:
        for sub in case.get("print_must_contain", []):
            if not any(sub in (p.print_text or "") for p in refined):
                print(f"FAIL {cid}: print missing substring {sub!r}")
                failed += 1
                ok = False
                break

    if ok:
        print(f"PASS {cid}")
        passed += 1

print(f"\nGolden summary: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
PY
