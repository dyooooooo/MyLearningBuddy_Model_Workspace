"""Score printed_toc_agent against the frozen answers, stage by stage.

Run inside textbook-agent (tools on /data/agent/tools):

    python3 /data/agent/eval/printed_toc/stage_report.py --arch v2 --save /tmp/cmp/v2
    python3 /data/agent/eval/printed_toc/stage_report.py --compare /tmp/cmp/v1 /tmp/cmp/v2

Stages: ``catalog`` rows straight from the print (``--catalog``, no model judging),
``structure`` final labels and levels with pages ignored, ``final`` labels, levels
and pages. Books are split so rules are tuned on ``train`` and checked on ``held``.
Each run writes toc.print in the book folder; ``--save`` keeps a copy per arch.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_ROOT = HERE.parents[1]
for cand in (_ROOT / "tools", _ROOT / "workspace" / "tools"):
    if cand.is_dir() and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))

import printed_toc_agent as agent  # noqa: E402
import printed_toc_tools as tools  # noqa: E402

BOOKS = {
    "0d100cbe267f45df91551d55b851c41c": ("生物", "train"),
    "13036f17d454435888c0f499e9773b82": ("数学", "train"),
    "1efec97d2afa4b1195bc1268e352e1f7": ("化学", "train"),
    "5ad5b2b8265c429292d63b0d88defb43": ("语文", "train"),
    "c1456a5d814c4ef0bcb8095d6674a649": ("英必1", "train"),
    "e5599c5a08924adca1efdcc0dbfc9416": ("英选2", "train"),
    "4c885e9a969e43e69fa6ab2965746e53": ("物理", "held"),
    "3cd3b0f7e76942849f474dc7365e4721": ("英必2", "held"),
    "42041c67e0c54196ae6082406f8b083e": ("英选3", "held"),
    "d6f21dedb0954d1eb5881da335dfed56": ("课标", "held"),
}
STRUCTURE = ("missing", "extra", "label", "level", "over_merge")
FINAL = STRUCTURE + ("page",)


def gold(book_id: str) -> dict[str, Any]:
    return json.loads((HERE / f"{book_id}.json").read_text(encoding="utf-8"))


def score(gold_entries: list[dict[str, Any]], got: list[dict[str, Any]]) -> dict[str, Any]:
    diff = tools.compare(gold_entries, got)
    return {
        "structure": {k: len(diff[k]) for k in STRUCTURE if diff[k]},
        "final": {k: len(diff[k]) for k in FINAL if diff[k]},
        "diff": {k: diff[k][:6] for k in FINAL if diff[k]},
    }


def catalog_entries(book_id: str, pages: list[int], arch: str) -> list[dict[str, Any]]:
    rows, *_ = tools._catalog(tools.book_dir(book_id), pages, model_wraps=arch == "v1")
    return [{"label": r["text"], "level": r["level"], "print_page": r.get("print_page")} for r in rows]


def run(arch: str, books: list[str], save: Path | None, catalog: bool) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for book_id in books:
        name, split = BOOKS[book_id]
        answer = gold(book_id)
        pages = ",".join(str(p) for p in answer.get("toc_pages") or [])
        started = time.perf_counter()
        result = agent.printed_toc_agent(book_id, pages=pages, force=True, arch=arch)
        path = Path(result["file"])
        doc = json.loads(path.read_text(encoding="utf-8"))
        item = {"name": name, "split": split, "seconds": round(time.perf_counter() - started, 1)}
        item.update(score(answer["entries"], doc.get("entries") or []))
        item["needs_review"] = result.get("needs_review", 0)
        if catalog:
            item["catalog"] = score(answer["entries"], catalog_entries(book_id, answer["toc_pages"], arch))["structure"]
        if save is not None:
            save.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, save / f"{book_id}.json")
        report[book_id] = item
        print(_line(item), flush=True)
    return report


def from_files(folder: Path) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for book_id, (name, split) in BOOKS.items():
        path = folder / f"{book_id}.json"
        if not path.is_file():
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        item = {"name": name, "split": split, "seconds": (doc.get("timing_s") or {}).get("total")}
        item.update(score(gold(book_id)["entries"], doc.get("entries") or []))
        item["needs_review"] = sum(1 for e in doc.get("entries") or [] if e.get("needs_review"))
        report[book_id] = item
    return report


def _mark(bucket: dict[str, int]) -> str:
    return "过" if not bucket else " ".join(f"{k}{v}" for k, v in bucket.items())


def _line(item: dict[str, Any]) -> str:
    return (f"{item['name']:<4} {item['split']:<5} 结构 {_mark(item['structure']):<22} "
            f"含页码 {_mark(item['final']):<26} 待审 {item.get('needs_review', 0)} {item.get('seconds')}s")


def summary(report: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for split in ("train", "held", "all"):
        items = [r for r in report.values() if split == "all" or r["split"] == split]
        out[split] = {
            "books": len(items),
            "structure_pass": sum(1 for r in items if not r["structure"]),
            "final_pass": sum(1 for r in items if not r["final"]),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=agent.ARCHES, default="v2")
    parser.add_argument("--books", default="", help="comma separated book ids; empty = all frozen")
    parser.add_argument("--save", default="")
    parser.add_argument("--catalog", action="store_true")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"))
    args = parser.parse_args()
    if args.compare:
        left, right = (from_files(Path(p)) for p in args.compare)
        for book_id, (name, split) in BOOKS.items():
            a, b = left.get(book_id), right.get(book_id)
            if a is None or b is None:
                continue
            print(f"{name:<4} {split:<5} 结构 {_mark(a['structure'])} → {_mark(b['structure'])}   "
                  f"含页码 {_mark(a['final'])} → {_mark(b['final'])}")
        print("A", json.dumps(summary(left), ensure_ascii=False))
        print("B", json.dumps(summary(right), ensure_ascii=False))
        return
    books = [b.strip() for b in args.books.split(",") if b.strip()] or list(BOOKS)
    report = run(args.arch, books, Path(args.save) if args.save else None, args.catalog)
    print(json.dumps(summary(report), ensure_ascii=False))


if __name__ == "__main__":
    main()
