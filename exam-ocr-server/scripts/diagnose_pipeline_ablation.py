#!/usr/bin/env python3
"""Isolate pipeline stages to find where handwriting hallucination is introduced.

Usage (from repo root, vLLM + buddy-api reachable):
  PYTHONPATH=exam-ocr-server python3 exam-ocr-server/scripts/diagnose_pipeline_ablation.py \\
    --frames-dir exam-ocr-server/tmp/ablation

Stages per frame:
  1. vision   — analyze_page only
  2. refine   — refine_parts on vision draft (with RAG)
  3. refine_no_rag — refine_parts with RAG stubbed empty
  4. merge    — offline merge_parts on saved per-frame outputs (no vLLM)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Allow running from workspace root
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.companion import merge as merge_mod
from app.ocr_refine import refine_parts, refine_parts_handwriting_only
from app.schemas import PagePart
from app.vllm_client import vllm_client


def _summarize_parts(parts: list[PagePart], label: str) -> dict[str, Any]:
    questions = [p for p in parts if p.kind == "question"]
    with_hw = [p for p in questions if (p.handwriting or "").strip()]
    hw_no_bbox = [
        p
        for p in with_hw
        if not p.bbox_handwriting or len(p.bbox_handwriting) < 4
    ]
    rows = []
    for p in sorted(questions, key=lambda x: int(x.id or 0)):
        hw = (p.handwriting or "").strip()
        bb = bool(p.bbox_handwriting and len(p.bbox_handwriting) >= 4)
        rows.append(
            {
                "id": p.id,
                "hw_len": len(hw),
                "has_bbox": bb,
                "handwriting": hw[:60],
            }
        )
    return {
        "label": label,
        "question_count": len(questions),
        "with_handwriting": len(with_hw),
        "handwriting_without_bbox": len(hw_no_bbox),
        "suspect_ids": [p.id for p in hw_no_bbox],
        "rows": rows,
    }


def _print_summary(s: dict[str, Any]) -> None:
    print(f"\n=== {s['label']} ===")
    print(
        f"  questions={s['question_count']}  "
        f"with_hw={s['with_handwriting']}  "
        f"hw_no_bbox={s['handwriting_without_bbox']}  "
        f"suspect_ids={s['suspect_ids']}"
    )
    for r in s["rows"]:
        if r["hw_len"]:
            flag = "⚠ no bbox" if not r["has_bbox"] else "ok"
            print(f"    Q{r['id']}: {r['handwriting']!r} [{flag}]")


async def _vision(image_bytes: bytes) -> list[PagePart]:
    result = await vllm_client.analyze_page(image_bytes, "image/jpeg")
    return result.parts


async def _refine(
    parts: list[PagePart],
    *,
    use_rag: bool,
    student_id: str = "student-01",
) -> list[PagePart]:
    if use_rag:
        refined, _ = await refine_parts(
            parts,
            student_id=student_id,
            subject="math",
            grade_band="hs",
        )
        return refined

    async def _empty_rag(*_a, **_k):
        return []

    import app.ocr_refine as ocr_refine_mod

    orig = ocr_refine_mod.rag_search
    ocr_refine_mod.rag_search = _empty_rag
    try:
        refined, _ = await refine_parts(
            parts,
            student_id=student_id,
            subject="math",
            grade_band="hs",
        )
    finally:
        ocr_refine_mod.rag_search = orig
    return refined


async def _refine_hw_only(
    parts: list[PagePart],
    *,
    student_id: str = "student-01",
) -> list[PagePart]:
    refined, _ = await refine_parts_handwriting_only(
        parts,
        student_id=student_id,
        subject="math",
        grade_band="hs",
    )
    return refined


def _merge_sequence(frame_parts: list[list[PagePart]], label: str) -> dict[str, Any]:
    merged: list[PagePart] = []
    for i, parts in enumerate(frame_parts, start=1):
        if not merged:
            merged = [p.model_copy(deep=True) for p in parts]
        else:
            merged = merge_mod.merge_parts(merged, parts)
    return _summarize_parts(merged, label)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline stage ablation for OCR hallucination")
    parser.add_argument(
        "--frames-dir",
        type=Path,
        default=_ROOT / "tmp" / "ablation",
        help="Directory with frame1.jpg, frame2.jpg, frame3.jpg",
    )
    parser.add_argument(
        "--only-frame",
        type=int,
        default=0,
        help="Run vision/refine only for this frame number (1-3); 0 = all",
    )
    parser.add_argument(
        "--skip-refine",
        action="store_true",
        help="Skip refine stages (vision + merge only)",
    )
    parser.add_argument(
        "--hw-only-refine",
        action="store_true",
        help="Also run experimental refine+RAG only on questions with handwriting",
    )
    args = parser.parse_args()

    frames_dir = args.frames_dir
    frame_files = sorted(frames_dir.glob("frame*.jpg"))
    if not frame_files:
        print(f"No frame*.jpg under {frames_dir}", file=sys.stderr)
        return 1

    results: dict[str, Any] = {"frames": {}, "merge": {}}
    vision_by_frame: list[list[PagePart]] = []
    refine_by_frame: list[list[PagePart]] = []
    refine_hw_by_frame: list[list[PagePart]] = []

    for path in frame_files:
        # frame1.jpg -> 1
        num = int("".join(c for c in path.stem if c.isdigit()) or "0")
        if args.only_frame and num != args.only_frame:
            continue

        image_bytes = path.read_bytes()
        print(f"\n{'='*60}\nFrame {num}: {path.name} ({len(image_bytes)//1024} KiB)")

        print("  [1/3] vision (analyze_page) …")
        v_parts = await _vision(image_bytes)
        v_sum = _summarize_parts(v_parts, f"frame{num} · vision only")
        _print_summary(v_sum)
        results["frames"][f"frame{num}"] = {"vision": v_sum}
        vision_by_frame.append(v_parts)

        if args.skip_refine:
            continue

        print("  [2/3] refine + RAG …")
        r_parts = await _refine(v_parts, use_rag=True)
        r_sum = _summarize_parts(r_parts, f"frame{num} · vision → print-only refine+RAG")
        _print_summary(r_sum)
        results["frames"][f"frame{num}"]["refine_rag"] = r_sum
        refine_by_frame.append(r_parts)

        print("  [3/3] refine, RAG disabled …")
        rn_parts = await _refine(v_parts, use_rag=False)
        rn_sum = _summarize_parts(rn_parts, f"frame{num} · vision → print-only refine (no RAG)")
        _print_summary(rn_sum)
        results["frames"][f"frame{num}"]["refine_no_rag"] = rn_sum

        if args.hw_only_refine:
            print("  [4/4] print-only refine + RAG (alias, diagnostic) …")
            rh_parts = await _refine_hw_only(v_parts)
            rh_sum = _summarize_parts(
                rh_parts, f"frame{num} · vision → print-only refine+RAG (alias)"
            )
            _print_summary(rh_sum)
            results["frames"][f"frame{num}"]["refine_hw_only_rag"] = rh_sum
            refine_hw_by_frame.append(rh_parts)

    if len(vision_by_frame) >= 2:
        print(f"\n{'='*60}\nOffline merge (no vLLM)")
        m_v = _merge_sequence(vision_by_frame, "merge · vision-only frames 1→N")
        _print_summary(m_v)
        results["merge"]["vision_only"] = m_v

        if refine_by_frame and len(refine_by_frame) == len(vision_by_frame):
            m_r = _merge_sequence(refine_by_frame, "merge · print-only refine+RAG frames 1→N")
            _print_summary(m_r)
            results["merge"]["refine_rag"] = m_r

        if refine_hw_by_frame and len(refine_hw_by_frame) == len(vision_by_frame):
            m_h = _merge_sequence(
                refine_hw_by_frame, "merge · refine+RAG hw-only frames 1→N"
            )
            _print_summary(m_h)
            results["merge"]["refine_hw_only_rag"] = m_h

    out_path = frames_dir / "ablation_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")

    await vllm_client.aclose()

    # Verdict helper
    print(f"\n{'='*60}\nINTERPRETATION")
    for key, block in results["frames"].items():
        v = block["vision"]
        rr = block.get("refine_rag")
        rn = block.get("refine_no_rag")
        if not rr:
            continue
        v_n = v["with_handwriting"]
        rr_n = rr["with_handwriting"]
        rn_n = rn["with_handwriting"] if rn else v_n
        print_changes = sum(
            1
            for vp, rp in zip(
                [p for p in vision_by_frame[int(key.replace("frame", "")) - 1] if p.kind == "question"],
                [p for p in refine_by_frame[int(key.replace("frame", "")) - 1] if p.kind == "question"],
                strict=False,
            )
            if (vp.print_text or "") != (rp.print_text or "")
        ) if refine_by_frame else 0
        if rr_n > v_n:
            print(f"  {key}: print-only refine ADDED handwriting ({v_n} → {rr_n}) — BUG")
        elif v_n > 0 and rr_n == v_n:
            print(
                f"  {key}: print-only refine kept hw count={v_n}; "
                f"print fields changed on {print_changes} questions"
            )
        if rn and rn_n < rr_n:
            print(f"  {key}: RAG contributed ({rn_n} hw without RAG vs {rr_n} with RAG)")
        rh = block.get("refine_hw_only_rag")
        if rh and v:
            rh_n = rh["with_handwriting"]
            if rh_n > v["with_handwriting"]:
                print(
                    f"  {key}: hw-only refine STILL expanded "
                    f"({v['with_handwriting']} → {rh_n})"
                )
            elif rh_n == v["with_handwriting"]:
                print(
                    f"  {key}: hw-only refine kept hw count={rh_n} "
                    f"(no expansion; suspect_ids={rh['suspect_ids']})"
                )
        if v_n >= rr_n and v["handwriting_without_bbox"] == 0 and rr["handwriting_without_bbox"] > 0:
            print(f"  {key}: refine introduced hw without bbox")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
