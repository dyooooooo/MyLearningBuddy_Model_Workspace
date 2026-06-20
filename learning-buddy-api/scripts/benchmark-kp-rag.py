#!/usr/bin/env python3
"""Benchmark kp tagging: top-1 / top-3 hit rate on labeled cases."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag.indexer import index_corpus  # noqa: E402
from app.rag.retriever import map_question_to_kp, search  # noqa: E402


def load_cases(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def run_benchmark(cases_path: Path, min_top1: float) -> int:
    index_corpus(force=False)
    cases = load_cases(cases_path)
    if not cases:
        print("No benchmark cases")
        return 1

    top1 = 0
    top3 = 0
    misses: list[str] = []

    for i, case in enumerate(cases, 1):
        text = case["print_text"]
        expected = case["expected_kp_id"]
        hits = search(text, top_k=5)
        kp_order = []
        seen: set[str] = set()
        for h in hits:
            if h.kp_id and h.kp_id not in seen:
                seen.add(h.kp_id)
                kp_order.append(h.kp_id)

        tags, _ = map_question_to_kp(text)
        tagged = tags[0]["kp_id"] if tags else None

        if kp_order and kp_order[0] == expected:
            top1 += 1
        elif tagged == expected:
            top1 += 1
        else:
            misses.append(f"  #{i} expected={expected} top={kp_order[:3]} tagged={tagged} | {text[:50]}")

        if expected in kp_order[:3] or tagged == expected:
            top3 += 1

    n = len(cases)
    r1 = top1 / n
    r3 = top3 / n
    print(f"Cases: {n}")
    print(f"Top-1: {top1}/{n} = {r1:.1%}")
    print(f"Top-3: {top3}/{n} = {r3:.1%}")
    if misses:
        print("Misses:")
        print("\n".join(misses[:10]))
        if len(misses) > 10:
            print(f"  ... and {len(misses) - 10} more")

    if r1 < min_top1:
        print(f"FAIL: top-1 {r1:.1%} < threshold {min_top1:.1%}")
        return 1
    print("PASS benchmark")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "data" / "benchmark" / "kp_label_cases.jsonl",
    )
    parser.add_argument("--min-top1", type=float, default=0.70)
    args = parser.parse_args()
    raise SystemExit(run_benchmark(args.cases, args.min_top1))


if __name__ == "__main__":
    main()
