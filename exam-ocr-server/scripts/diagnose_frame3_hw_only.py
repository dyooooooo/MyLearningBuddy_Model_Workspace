#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ocr_refine import refine_parts, refine_parts_handwriting_only
from app.vllm_client import vllm_client


async def summarize(parts, label: str) -> None:
    qs = [p for p in parts if p.kind == "question"]
    with_hw = [p for p in qs if (p.handwriting or "").strip()]
    no_bbox = [p for p in with_hw if not p.bbox_handwriting or len(p.bbox_handwriting) < 4]
    print(f"\n=== {label} === with_hw={len(with_hw)} suspect={[p.id for p in no_bbox]}")
    for p in sorted(qs, key=lambda x: int(x.id or 0)):
        hw = (p.handwriting or "").strip()
        if hw:
            bb = bool(p.bbox_handwriting and len(p.bbox_handwriting) >= 4)
            print(f"  Q{p.id}: {hw[:60]!r} [{'ok' if bb else 'no bbox'}]")


async def main() -> None:
    img = Path("/app/tmp/ablation/frame3.jpg").read_bytes()
    v = (await vllm_client.analyze_page(img, "image/jpeg")).parts
    await summarize(v, "vision only")
    r, _ = await refine_parts(v, student_id="student-01")
    await summarize(r, "refine+RAG (full)")
    h, _ = await refine_parts_handwriting_only(v, student_id="student-01")
    await summarize(h, "refine+RAG (hw-only)")
    await vllm_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
