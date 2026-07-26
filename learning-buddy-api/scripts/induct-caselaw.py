#!/usr/bin/env python3
"""离线归纳 CLI：从判例库聚合规则提议 + 过晋级门（golden 全绿），写提议文件。

晋级治理硬约束：本脚本**只生成提议**，绝不自动写规则库 / 抑制表 / prompt。
人审核 promotable 项后手动落 import35b_suppressions / repair_rules。

用法（一行一条命令）：

  PYTHONPATH=. python3 scripts/induct-caselaw.py

  PYTHONPATH=. python3 scripts/induct-caselaw.py --min-occurrences 5 --out data/eval/caselaw_proposals/latest.json

退出码：晋级门 golden 破坏 → 1（基线已坏，任何晋级都不安全）；否则 0。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.db.session import get_session_factory
from app.import_35b.caselaw import induction


def main() -> int:
    ap = argparse.ArgumentParser(description="35B 判例库离线归纳 + 晋级门")
    ap.add_argument("--min-occurrences", type=int, default=3, help="同指纹最少复现次数")
    ap.add_argument("--out", type=str, default="", help="提议文件输出路径（默认按时间戳落 data/eval/caselaw_proposals/）")
    args = ap.parse_args()

    factory = get_session_factory()
    with factory() as session:
        report = induction.build_report(session, min_occurrences=args.min_occurrences)

    if args.out:
        out_path = Path(args.out)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = Path("data/eval/caselaw_proposals") / f"{ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    gate = report["gate"]
    print(f"判例总数: {report['caselaw_total']} (误判 {report['misjudgment_total']} / 方案 {report['solution_total']})")
    print(f"晋级门 golden: {'PASS' if gate['passed'] else 'FAIL'} ({gate['golden_total']} 例)")
    if not gate["passed"]:
        for f in gate["golden_failures"]:
            print(f"  golden FAIL: {f}")
    print(f"可晋级提议: {len(report['promotable'])} · 受阻提议: {len(report['blocked'])}")
    for p in report["promotable"]:
        print(f"  ✅ [{p['kind']}] {p['suspect_type']} fp={p['fingerprint']} x{p['occurrences']}")
        print(f"     → {p['action_hint']}")
    for p in report["blocked"]:
        why = "；".join(p.get("reasons") or []) or ("冲突" if p.get("conflict") else "未过门")
        print(f"  ⏸ [{p['kind']}] {p['suspect_type']} fp={p['fingerprint']} x{p['occurrences']} · slot={p['target_slot']} · {why}")
    print(f"提议文件: {out_path}")

    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
