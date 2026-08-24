"""Replay the layout decision layer over a real import and report what changes.

Answers the two questions you need before turning ``refine_mode: auto`` on:
how many pages the rules move, and how many pages would spend a 35B call.

    python -m scripts.audit_layout_decisions            # newest import job
    python -m scripts.audit_layout_decisions --job 42 --sample 6
"""
from __future__ import annotations

import argparse
import collections
from typing import Iterable

from app.db.models import CorpusImportJob
from app.db.session import get_session_factory
from app.services.document_ir import DocumentBlock
from app.services.import_ir_store import load_blocks
from app.services.ir.layout_enrich import enrich_page_blocks_slim_with_decision
from app.services.ir.textbook_profile import default_profile_id, load_textbook_profile
from app.util.json_util import loads


def _latest_job_id(session) -> int | None:
    row = (
        session.query(CorpusImportJob)
        .order_by(CorpusImportJob.id.desc())
        .first()
    )
    return row.id if row else None


def _by_page(blocks: Iterable[DocumentBlock]) -> dict[int, list[DocumentBlock]]:
    pages: dict[int, list[DocumentBlock]] = collections.defaultdict(list)
    for b in blocks:
        pages[int(b.page_no or 0)].append(b)
    return dict(sorted(pages.items()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", type=int, default=None)
    ap.add_argument("--sample", type=int, default=5, help="pages to print in detail")
    args = ap.parse_args()

    session = get_session_factory()()
    try:
        job_id = args.job or _latest_job_id(session)
        if job_id is None:
            print("no import jobs")
            return 1
        job = session.get(CorpusImportJob, job_id)
        if job is None:
            print(f"job {job_id} not found")
            return 1
        meta = loads(job.meta_json, {})
        edition_id = meta.get("edition_id") or meta.get("profile_id")
        blocks = load_blocks(job)
    finally:
        session.close()

    if not blocks:
        print(f"job {job_id} has no blocks.json")
        return 1

    profile = load_textbook_profile(edition_id or default_profile_id())
    page_w = float(profile.get("geometry", {}).get("page_w") or 1000.0)
    page_h = page_w * 1.414
    print(f"job={job_id} edition={edition_id} blocks={len(blocks)}")

    def _enrich(page_blocks):
        return enrich_page_blocks_slim_with_decision(
            page_blocks, profile=profile, page_w=page_w, page_h=page_h
        )

    pages = _by_page(blocks)
    changed_pages: list[tuple[int, int]] = []
    escalate_pages: list[int] = []
    rule_hits: collections.Counter[str] = collections.Counter()
    zone_moves: collections.Counter[str] = collections.Counter()

    for page_no, page_blocks in pages.items():
        before = {b.id: (b.meta or {}).get("layout_zone", "main") for b in page_blocks}
        after_blocks, decision = _enrich(page_blocks)
        moved = 0
        for b in after_blocks:
            new_zone = (b.meta or {}).get("layout_zone", "main")
            old_zone = before.get(b.id, "main")
            rid = (b.meta or {}).get("layout_rule_id")
            if rid:
                rule_hits[rid] += 1
            if new_zone != old_zone:
                moved += 1
                zone_moves[f"{old_zone} -> {new_zone}"] += 1
        if moved:
            changed_pages.append((page_no, moved))
        if decision is not None and decision.escalate:
            escalate_pages.append(page_no)

    total = len(pages)
    print(f"\npages={total}")
    print(
        f"pages with zone change: {len(changed_pages)} "
        f"({len(changed_pages) / total:.0%})"
    )
    print(
        f"pages that would escalate to 35B: {len(escalate_pages)} "
        f"({len(escalate_pages) / total:.0%})"
    )

    if zone_moves:
        print("\nzone moves:")
        for move, n in zone_moves.most_common():
            print(f"  {move:28s} {n}")

    print("\nrule hits:")
    for rid, n in rule_hits.most_common(15):
        print(f"  {rid:44s} {n}")

    if escalate_pages:
        head = ", ".join(str(p) for p in escalate_pages[:30])
        more = " ..." if len(escalate_pages) > 30 else ""
        print(f"\nescalate pages: {head}{more}")

    for page_no, moved in changed_pages[: args.sample]:
        print(f"\n--- page {page_no}: {moved} block(s) moved ---")
        page_blocks = pages[page_no]
        before = {b.id: (b.meta or {}).get("layout_zone", "main") for b in page_blocks}
        after_blocks, _ = _enrich(page_blocks)
        for b in after_blocks:
            meta = b.meta or {}
            new_zone = meta.get("layout_zone", "main")
            if new_zone == before.get(b.id, "main"):
                continue
            text = (b.text or "").replace("\n", " ")[:40]
            print(
                f"  {before.get(b.id, 'main'):12s} -> {new_zone:12s} "
                f"conf={meta.get('layout_confidence_score')} "
                f"rule={meta.get('layout_rule_id')} bbox={b.bbox} :: {text}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
