#!/usr/bin/env python3
"""Clean improvement points to the final-success-only model.

Run inside buddy-api container (has app + DB access)::

    docker exec -i buddy-api python - < tools/ip-cleanup.py --dry-run
    docker exec -i buddy-api python - < tools/ip-cleanup.py --apply

Or copy into the container and execute. Writes a Markdown report under
``/workspace/doc/reports/`` when that path is mounted; otherwise
``/tmp/ip-cleanup-report.md``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Allow running as stdin into docker exec
sys.path.insert(0, "/app")

from app.db.migrate import apply_pilot_migrations  # noqa: E402
from app.db.session import get_engine, get_session_factory  # noqa: E402
from app.db.models import (  # noqa: E402
    CorpusImprovementLink,
    CorpusImprovementPattern,
    CorpusImprovementPoint,
)
from app.services.improvement_points import (  # noqa: E402
    guess_needs_split,
    infer_category,
    normalize_origin,
)
from app.util.json_util import dumps, loads  # noqa: E402

PROBE_JOBS = frozenset(
    {"smoke-job", "accept-ui-probe-1785388485"}
)
PROBE_SESSIONS = frozenset({"accept-ui"})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _claim(row: CorpusImprovementPoint) -> dict:
    c = loads(row.claim_json, {})
    return c if isinstance(c, dict) else {}


def _proc_steps(row: CorpusImprovementPoint) -> list:
    p = loads(row.procedure_json, {})
    if not isinstance(p, dict):
        return []
    steps = p.get("steps") or []
    return steps if isinstance(steps, list) else []


def _intent_mismatch(claim: dict, summary: str) -> bool:
    ui = str(claim.get("user_input") or claim.get("user_prompt") or "").strip()
    if not ui:
        return False
    # Very coarse: no shared CJK bigrams between user ask and summary
    sm = summary or ""
    if not sm:
        return True
    ui_chars = {ui[i : i + 2] for i in range(len(ui) - 1) if "\u4e00" <= ui[i] <= "\u9fff"}
    sm_chars = {sm[i : i + 2] for i in range(len(sm) - 1) if "\u4e00" <= sm[i] <= "\u9fff"}
    if not ui_chars:
        return False
    overlap = ui_chars & sm_chars
    return len(overlap) < 2


def run(*, apply: bool) -> str:
    apply_pilot_migrations(get_engine())
    sf = get_session_factory()
    session = sf()
    report: list[str] = []
    stamp = _now()
    report.append(f"# 改进点清理报告 ({stamp})")
    report.append("")
    report.append(f"- 模式: `{'apply' if apply else 'dry-run'}`")
    report.append("")

    rows = session.query(CorpusImprovementPoint).all()
    before = Counter(r.status for r in rows)
    report.append("## 清理前")
    report.append(f"- 总数: {len(rows)}")
    report.append(f"- 按 status: {dict(before)}")
    report.append("")

    to_delete: list[CorpusImprovementPoint] = []
    keep: list[CorpusImprovementPoint] = []
    intent_candidates: list[tuple[CorpusImprovementPoint, str]] = []
    migrated_note: list[str] = []

    for r in rows:
        claim = _claim(r)
        summary = str(getattr(r, "summary", None) or claim.get("summary") or "")
        origin_raw = str(claim.get("origin") or getattr(r, "origin", None) or "")
        job = str(r.source_job_id or "")
        sess = str(r.source_session_id or "")

        # Delete probes / pipeline / superseded / no procedure
        if job in PROBE_JOBS or sess in PROBE_SESSIONS:
            to_delete.append(r)
            continue
        if origin_raw in ("test_probe", "pipeline_event"):
            to_delete.append(r)
            continue
        if str(r.status or "") == "superseded":
            to_delete.append(r)
            continue
        if str(r.status or "") not in ("accepted", "effective"):
            # draft/conflict/etc. — not final success
            if not _proc_steps(r):
                to_delete.append(r)
                continue
            # keep only if looks like accepted-equivalent with steps
            to_delete.append(r)
            continue
        if not _proc_steps(r):
            to_delete.append(r)
            continue

        keep.append(r)
        if origin_raw == "migrated_l3":
            migrated_note.append(
                f"- p{r.page_no} `{r.id[:8]}` {summary[:120]}"
            )
        if _intent_mismatch(claim, summary):
            intent_candidates.append((r, summary))

    report.append("## 将删除")
    report.append(f"- 条数: {len(to_delete)}")
    del_reasons = Counter()
    for r in to_delete:
        claim = _claim(r)
        o = str(claim.get("origin") or "")
        if r.source_job_id in PROBE_JOBS or o == "test_probe":
            del_reasons["probe"] += 1
        elif o == "pipeline_event":
            del_reasons["pipeline_event"] += 1
        elif r.status == "superseded":
            del_reasons["superseded"] += 1
        elif not _proc_steps(r):
            del_reasons["no_procedure"] += 1
        else:
            del_reasons["other_status"] += 1
    report.append(f"- 原因分布: {dict(del_reasons)}")
    report.append("")

    report.append("## 将保留并回填")
    report.append(f"- 条数: {len(keep)}")
    report.append("")

    # Near-duplicate mark (same page + same summary prefix)
    dup_groups: dict[tuple, list] = {}
    for r in keep:
        claim = _claim(r)
        summary = str(getattr(r, "summary", None) or claim.get("summary") or "")
        key = (r.fingerprint_hash[:12], int(r.page_no or 0), summary[:40])
        dup_groups.setdefault(key, []).append(r)
    near_dups = {k: v for k, v in dup_groups.items() if len(v) > 1}

    split_list: list[str] = []
    origin_ctr: Counter[str] = Counter()

    for r in keep:
        claim = _claim(r)
        summary = str(getattr(r, "summary", None) or claim.get("summary") or "")
        kind = str(getattr(r, "kind", None) or claim.get("kind") or "")
        origin_raw = str(claim.get("origin") or getattr(r, "origin", None) or "")
        origin = normalize_origin(origin_raw) or "user_ask"
        # Heuristic from summary for batch AI
        if re.search(r"第\s*\d+\s*批", summary) or "必修二第" in summary:
            if origin_raw in ("", "agent_page", "fullbook_ai"):
                origin = "ai_auto"
        needs = 1 if guess_needs_split(summary) else 0
        if needs:
            split_list.append(f"- p{r.page_no} [{origin}] {summary}")

        new_cat = infer_category(summary, kind, "")
        old_cat = str(r.category or "")
        if new_cat != "other" and old_cat and new_cat != old_cat:
            # only fix obvious wrong when old was figure swallowing others
            if old_cat == "figure" and new_cat in ("table", "math", "title", "exercise"):
                r.category = new_cat

        r.origin = origin
        r.summary = summary[:2000]
        r.kind = (kind or "")[:64]
        r.needs_split = needs
        r.status = "accepted"
        r.pattern_status = "none"
        r.pattern_id = ""
        # keep claim.origin in sync
        claim["origin"] = origin
        if summary and not claim.get("summary"):
            claim["summary"] = summary[:400]
        r.claim_json = dumps(claim)
        r.updated_at = datetime.now(timezone.utc).isoformat()
        origin_ctr[origin] += 1

    # Unlink patterns from deleted + kept (kept already cleared pattern_id)
    del_ids = {r.id for r in to_delete}
    if apply:
        # Remove links involving deleted rows
        if del_ids:
            links = session.query(CorpusImprovementLink).all()
            for lk in links:
                if lk.from_id in del_ids or lk.to_id in del_ids:
                    session.delete(lk)
        for r in to_delete:
            session.delete(r)
        # Reset pattern member_count loosely
        for pat in session.query(CorpusImprovementPattern).all():
            pat.member_count = 0
        session.commit()
    else:
        session.rollback()

    report.append("## 来源分布（保留集）")
    for k, v in origin_ctr.most_common():
        report.append(f"- `{k}`: {v}")
    report.append("")

    report.append("## 历史迁移行（mapped → user_ask，抽查用）")
    report.append(f"- 条数: {len(migrated_note)}")
    report.extend(migrated_note[:40] or ["- （无）"])
    if len(migrated_note) > 40:
        report.append(f"- … 另有 {len(migrated_note) - 40} 条")
    report.append("")

    report.append("## 待拆（needs_split）")
    report.append(f"- 条数: {len(split_list)}")
    report.extend(split_list or ["- （无）"])
    report.append("")

    report.append("## 意图未兑现候选（未自动删除，请人过目）")
    report.append(f"- 条数: {len(intent_candidates)}")
    for r, sm in intent_candidates[:50]:
        claim = _claim(r)
        ui = str(claim.get("user_input") or "")[:200]
        report.append(f"- p{r.page_no} `{r.id[:8]}`")
        report.append(f"  - 用户原话: {ui or '（空）'}")
        report.append(f"  - 摘要: {sm}")
    if len(intent_candidates) > 50:
        report.append(f"- … 另有 {len(intent_candidates) - 50} 条")
    report.append("")

    report.append("## 近义重复候选（同页摘要前 40 字相同，未自动合并）")
    report.append(f"- 组数: {len(near_dups)}")
    for (fp, page, pref), group in list(near_dups.items())[:20]:
        report.append(f"- fp={fp} p{page} n={len(group)} | {pref}")
    report.append("")

    report.append("## 清理后（预期）")
    report.append(f"- 保留: {len(keep)}")
    report.append(f"- 删除: {len(to_delete)}")
    report.append("- 旧模式: 仅解绑成员（pattern_id 清空），未删模式表")
    report.append("")

    text = "\n".join(report) + "\n"
    out_candidates = [
        Path(f"/workspace/doc/reports/{stamp}-ip-cleanup.md"),
        Path(f"/app/doc/reports/{stamp}-ip-cleanup.md"),
        Path(f"/tmp/{stamp}-ip-cleanup.md"),
    ]
    # Also try host-mounted workspace via sibling path when running on host
    host_report = Path(__file__).resolve().parents[1] / "doc" / "reports" / f"{stamp}-ip-cleanup.md"
    out_candidates.insert(0, host_report)

    written = None
    for p in out_candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            written = str(p)
            break
        except OSError:
            continue
    if written:
        print(f"report: {written}", file=sys.stderr)
    else:
        print(text)
    print(
        json.dumps(
            {
                "apply": apply,
                "before": len(rows),
                "keep": len(keep),
                "delete": len(to_delete),
                "needs_split": len(split_list),
                "intent_candidates": len(intent_candidates),
                "origins": dict(origin_ctr),
                "report": written,
            },
            ensure_ascii=False,
        )
    )
    return text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    args = ap.parse_args()
    if not args.apply and not args.dry_run:
        args.dry_run = True
    if args.apply and args.dry_run:
        print("use only one of --dry-run / --apply", file=sys.stderr)
        sys.exit(2)
    run(apply=bool(args.apply))


if __name__ == "__main__":
    main()
