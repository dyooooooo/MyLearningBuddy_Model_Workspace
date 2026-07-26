#!/usr/bin/env python3
"""v4 回放棘轮：对历史 raw_model.json 重跑净化面+编译面+校验面（免 GPU）。

任何规则/代码改动前后各跑一次，对比 finding 数量与 HTML 指标——
回放零破坏是判例晋级为规则的硬门之一。

用法（一行一条命令）：

  PYTHONPATH=. python3 scripts/replay-import35b.py --dir /path/to/dumps

  PYTHONPATH=. python3 scripts/replay-import35b.py --dir .pageaudit/latest --json out.json

dump 目录接受 ``p0001_raw_model.json``（.pageaudit 布局）或
``p0001/raw_model.json``（MinIO debug 布局）两种命名。
硬不变量（CONTROL_CHAR_RESIDUE / ENV_UNBALANCED / MAIN_EMPTY）出现即退出码 1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from app.import_35b.model_boundary import repair_blueprint_dict
from app.import_35b.normalize import normalize_blueprint
from app.import_35b.page_compiler import compile_page_html
from app.import_35b.schemas import PageBlueprint
from app.import_35b.validation import run_page_checks

HARD_SUSPECTS = {"CONTROL_CHAR_RESIDUE", "ENV_UNBALANCED", "MAIN_EMPTY"}


def _find_dumps(root: Path) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for p in sorted(root.glob("p*_raw_model.json")):
        m = re.match(r"p(\d+)_raw_model\.json", p.name)
        if m:
            out.append((int(m.group(1)), p))
    for p in sorted(root.glob("p*/raw_model.json")):
        m = re.match(r"p(\d+)", p.parent.name)
        if m:
            out.append((int(m.group(1)), p))
    return out


def replay_page(page_no: int, raw: dict) -> dict:
    raw, repairs = repair_blueprint_dict(raw)
    raw.setdefault("page_no", page_no)
    raw.setdefault("page_size", [1000, 1000])
    raw.pop("column_count_hint", None)
    bp = normalize_blueprint(PageBlueprint.model_validate(raw))
    ssr_report: list[dict] = []
    html = compile_page_html(bp, ssr_report=ssr_report)
    findings = run_page_checks(bp, html, ssr_report=ssr_report)
    return {
        "page_no": page_no,
        "repairs": len(repairs),
        "repair_rules": sorted({r["rule"] for r in repairs}),
        "svg": html.count("<svg"),
        "math_error": html.count("math--error"),
        "math_fallback": html.count("math--fallback"),
        "data_latex_residue": html.count('data-latex="'),
        "findings": [
            {"suspect_type": f.suspect_type, "severity": f.severity, "block_id": f.block_id}
            for f in findings
        ],
        "hard_violations": sorted(
            {f.suspect_type for f in findings if f.suspect_type in HARD_SUSPECTS}
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, help="raw_model.json dump 目录")
    ap.add_argument("--json", default="", help="可选：完整结果落盘路径")
    ap.add_argument("--report-only", action="store_true", help="硬违约不改退出码")
    args = ap.parse_args()

    root = Path(args.dir)
    dumps = _find_dumps(root)
    if not dumps:
        print(f"no raw_model dumps found under {root}", file=sys.stderr)
        return 2

    results = []
    hard_pages = []
    for page_no, path in dumps:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            results.append({"page_no": page_no, "error": f"load: {exc}"})
            continue
        try:
            r = replay_page(page_no, raw)
        except Exception as exc:  # noqa: BLE001
            r = {"page_no": page_no, "error": str(exc)[:200]}
        results.append(r)
        if r.get("hard_violations"):
            hard_pages.append((page_no, r["hard_violations"]))
        print(
            f"p{page_no:04d} repairs={r.get('repairs', '-'):>3} svg={r.get('svg', '-'):>3} "
            f"err={r.get('math_error', '-')} fb={r.get('math_fallback', '-')} "
            f"findings={len(r.get('findings', []))} "
            f"hard={','.join(r.get('hard_violations', [])) or '-'}"
            + (f" ERROR={r['error']}" if r.get("error") else "")
        )

    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    total_findings = sum(len(r.get("findings", [])) for r in results)
    print(f"\npages={len(results)} findings={total_findings} hard_violation_pages={len(hard_pages)}")
    if hard_pages and not args.report_only:
        for page_no, kinds in hard_pages:
            print(f"  HARD p{page_no:04d}: {kinds}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
