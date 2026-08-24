#!/usr/bin/env python3
"""Rewrite IP summaries to 直奔主题 style (normalize + light polish).

Run inside buddy-api::

    docker cp tools/ip-rewrite-summaries.py buddy-api:/tmp/ip-rewrite-summaries.py
    docker exec -e PYTHONPATH=/app -w /app buddy-api python /tmp/ip-rewrite-summaries.py --dry-run
    docker exec -e PYTHONPATH=/app -w /app buddy-api python /tmp/ip-rewrite-summaries.py --apply
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app")

from app.db.migrate import apply_pilot_migrations  # noqa: E402
from app.db.models import CorpusImprovementPoint  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.improvement_points import normalize_direct_summary  # noqa: E402
from app.util.json_util import dumps, loads  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not args.dry_run and not args.apply:
        args.dry_run = True

    apply_pilot_migrations(get_engine())
    factory = get_session_factory()
    session = factory()
    changed: list[tuple[str, int, str, str]] = []
    try:
        rows = session.query(CorpusImprovementPoint).all()
        for r in rows:
            old = str(getattr(r, "summary", None) or "")
            claim = loads(r.claim_json, {}) or {}
            if not isinstance(claim, dict):
                claim = {}
            if not old:
                old = str(claim.get("summary") or "")
            new = normalize_direct_summary(old)
            claim_sum = normalize_direct_summary(str(claim.get("summary") or ""))
            if (
                claim_sum
                and len(claim_sum) > len(new) + 12
                and "记入当前" not in claim_sum
                and "避免" in new
                and "避免" not in claim_sum
            ):
                new = claim_sum
            if new == old:
                continue
            changed.append((r.id[:8], int(r.page_no or 0), old, new))
            if args.apply:
                r.summary = new[:2000]
                claim["summary"] = new[:400]
                r.claim_json = dumps(claim)
                r.updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if args.apply and changed:
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()

    mode = "apply" if args.apply else "dry-run"
    print(f"mode={mode} changed={len(changed)}")
    for pid, page, old, new in changed[:40]:
        print(f"p{page} {pid}")
        print(f"  - {old[:120]}")
        print(f"  + {new[:120]}")
    if len(changed) > 40:
        print(f"... +{len(changed) - 40} more")

    lines = [
        f"# 改进点摘要直奔主题改写 ({datetime.now().strftime('%Y-%m-%d')})",
        "",
        f"- 模式: `{mode}`",
        f"- 改写条数: {len(changed)}",
        "",
        "## 样例",
        "",
    ]
    for pid, page, old, new in changed[:80]:
        lines.append(f"- p{page} `{pid}`")
        lines.append(f"  - 旧: {old}")
        lines.append(f"  - 新: {new}")
    text = "\n".join(lines) + "\n"
    report = Path("/tmp") / f"{datetime.now().strftime('%Y-%m-%d')}-ip-summary-rewrite.md"
    report.write_text(text, encoding="utf-8")
    print("report", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
