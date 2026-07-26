#!/usr/bin/env python3
"""One-page live refine smoke test (MinerU blocks + 35B vLLM)."""
from __future__ import annotations

import json
import os
import sys

from app.clients.minio_client import get_object_bytes
from app.services.document_ir import document_blocks_from_dicts
from app.services.ir.apply_refine import apply_page_refine
from app.services.ir.layout_enrich import enrich_blocks_slim
from app.services.ir.mineru_page_refine import refine_page

PDF_KEY = (
    "corpus/imports/b4a3d19edb8a466cbbae829d1b878814/"
    "普通高中教科书·数学必修 第一册.pdf"
)
BLOCKS_KEY = "corpus/imports/b4a3d19edb8a466cbbae829d1b878814/assets/blocks.json"
PAGE_NO = 12


def main() -> int:
    os.environ.pop("SKIP_LIVE_REFINE", None)
    pdf = get_object_bytes(PDF_KEY)
    raw = get_object_bytes(BLOCKS_KEY)
    if not pdf or not raw:
        print("FAIL: missing minio objects", file=sys.stderr)
        return 1
    blocks = document_blocks_from_dicts(json.loads(raw.decode("utf-8")))
    print(f"loaded pdf={len(pdf)} blocks={len(blocks)}")
    result = refine_page(pdf, PAGE_NO, blocks, profile_id="hujiao_math_hs_v1")
    print(
        f"refine page={result.page_no} patches={len(result.blocks)} "
        f"chunks={len(result.chunks)}"
    )
    patched = apply_page_refine(blocks, result)
    enriched = enrich_blocks_slim(patched)
    page_blocks = [b for b in enriched if b.page_no == PAGE_NO]
    aside_right = sum(1 for b in page_blocks if b.meta.get("layout_zone") == "aside_right")
    print(f"page {PAGE_NO} aside_right={aside_right}")
    if aside_right < 1:
        print("WARN: expected aside_right on page 12", file=sys.stderr)
        return 2
    print("smoke_refine_page12 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
