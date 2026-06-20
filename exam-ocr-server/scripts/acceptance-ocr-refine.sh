#!/usr/bin/env bash
# Smoke test ocr_refine module (no vLLM required for structure check).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}"
cd "${ROOT}"

python3 << 'PY'
from app.schemas import PagePart
from app.ocr_refine import _count_changes, _parts_to_payload, _preserve_handwriting_from_draft
from app.companion import merge as merge_mod

parts = [
    PagePart(kind="question", id="1", print_text="已知全集 U", handwriting="A"),
]
payload = _parts_to_payload(parts)
assert payload[0]["print"] == "已知全集 U"
assert _count_changes(parts, parts) == 0

draft = [
    PagePart(
        kind="question",
        id="1",
        print_text="Q1",
        handwriting="x",
        bbox_handwriting=[0.1, 0.2, 0.3, 0.4],
    ),
    PagePart(kind="question", id="2", print_text="Q2", handwriting=""),
]
hallucinated = [
    PagePart(
        kind="question",
        id="1",
        print_text="Q1",
        handwriting="x2",
        bbox_handwriting=[0.1, 0.2, 0.3, 0.4],
    ),
    PagePart(
        kind="question",
        id="2",
        print_text="Q2",
        handwriting="42",
    ),
]
clamped = _preserve_handwriting_from_draft(draft, hallucinated)
assert clamped[0].handwriting == "x"
assert clamped[1].handwriting == ""

base = [PagePart(kind="question", id="1", print_text="Q", handwriting="")]
incoming = [PagePart(kind="question", id="1", print_text="Q", handwriting="fake answer")]
merged = merge_mod.merge_parts(base, incoming)
assert merged[0].handwriting == ""

print("PASS: ocr_refine + merge guards")
PY
