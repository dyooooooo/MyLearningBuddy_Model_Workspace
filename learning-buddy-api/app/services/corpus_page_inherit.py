"""Carry finished pages from a previous import job into a fresh one.

Why this exists: the L3 patch ledger (``corpus_corrections``) only replays rows
whose ``after`` holds a real block snapshot, so most hand/agent fixes from an
earlier import were unreplayable. The previous job's final IR *is* the result of
all those successful fixes, so inheriting whole pages from it recovers the work
deterministically — no agent re-run, no prompt replay.

Page numbers drift between parses (blank-page trimming, engine changes), so
pages are aligned by text similarity inside a small window instead of trusting
the page number.
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CorpusCorrection, CorpusImportJob, CorpusImprovementPoint
from app.services.document_ir import DocumentBlock
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")

SIGNATURE_LIMIT = 400
BLANK_SIGNATURE_LIMIT = 20
MATCH_THRESHOLD = 0.55
FALLBACK_THRESHOLD = 0.35
DEFAULT_MAX_OFFSET = 8


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _block_text(block: DocumentBlock) -> str:
    parts = [block.text or "", block.caption or ""]
    return " ".join(p for p in parts if p)


def page_signature(blocks: list[DocumentBlock], page_no: int) -> str:
    """Normalized text of one page, used only for alignment scoring."""
    page = sorted(
        (b for b in blocks if b.page_no == page_no), key=lambda b: (b.order, b.id)
    )
    raw = " ".join(_block_text(b) for b in page)
    return _WS_RE.sub(" ", raw).strip()[:SIGNATURE_LIMIT]


def _signatures(blocks: list[DocumentBlock]) -> dict[int, str]:
    pages = sorted({b.page_no for b in blocks})
    return {p: page_signature(blocks, p) for p in pages}


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _is_blank(signature: str) -> bool:
    """Image-only pages (covers, full-page figures) carry almost no text."""
    return len(signature.strip()) < BLANK_SIGNATURE_LIMIT


def improved_pages_for_job(
    session: Session, job: CorpusImportJob, *, fingerprint_hash: str = ""
) -> list[int]:
    """Pages the previous run actually improved (improvement points + patches)."""
    fp = (fingerprint_hash or "").strip()
    if not fp:
        meta = loads(job.meta_json, {})
        bf = meta.get("book_fingerprint") or {}
        if isinstance(bf, dict):
            fp = str(bf.get("hash") or "").strip()

    pages: set[int] = set()

    ip_q = session.query(CorpusImprovementPoint.page_no)
    if fp:
        ip_q = ip_q.filter(
            (CorpusImprovementPoint.fingerprint_hash == fp)
            | (CorpusImprovementPoint.source_job_id == job.id)
        )
    else:
        ip_q = ip_q.filter(CorpusImprovementPoint.source_job_id == job.id)
    for (page_no,) in ip_q.all():
        if page_no:
            pages.add(int(page_no))

    corr_q = session.query(CorpusCorrection.page_no)
    if fp:
        corr_q = corr_q.filter(
            (CorpusCorrection.fingerprint_hash == fp)
            | (CorpusCorrection.source_job_id == job.id)
        )
    else:
        corr_q = corr_q.filter(CorpusCorrection.source_job_id == job.id)
    for (page_no,) in corr_q.all():
        if page_no:
            pages.add(int(page_no))

    return sorted(pages)


def _align_pages(
    old_sigs: dict[int, str],
    new_sigs: dict[int, str],
    candidates: list[int],
    *,
    max_offset: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    """Map old pages onto new pages by text similarity within a window.

    Returns (matched, low_confidence, unmatched, offset_hint). Low-confidence
    rows are aligned only by the book-wide page offset — image-only pages such as
    covers, or pages the two parses read very differently.
    """
    new_pages = sorted(new_sigs)
    pending: list[tuple[int, float, int, str]] = []  # old, score, new, reason
    leftovers: list[dict[str, Any]] = []

    def _best(old_page: int, window: int) -> tuple[int, float]:
        best_page, best_score = 0, 0.0
        for new_page in new_pages:
            if abs(new_page - old_page) > window:
                continue
            score = _similarity(old_sigs.get(old_page, ""), new_sigs.get(new_page, ""))
            if score > best_score or (
                score == best_score
                and best_page
                and abs(new_page - old_page) < abs(best_page - old_page)
            ):
                best_page, best_score = new_page, score
        return best_page, best_score

    for old_page in candidates:
        best_page, best_score = _best(old_page, max_offset)
        if best_page and best_score >= MATCH_THRESHOLD:
            pending.append((old_page, best_score, best_page, "text"))
        else:
            leftovers.append(
                {
                    "old_page": old_page,
                    "best_new_page": best_page or None,
                    "best_score": round(best_score, 3),
                }
            )

    # Majority offset from confident matches guides the leftovers.
    offsets = [new - old for old, _score, new, _reason in pending]
    offset_hint = 0
    if offsets:
        offsets.sort()
        offset_hint = offsets[len(offsets) // 2]

    low_confidence: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for item in leftovers:
        old_page = int(item["old_page"])
        guess = old_page + offset_hint
        if guess not in new_sigs:
            item["offset_hint"] = offset_hint
            unmatched.append(item)
            continue
        score = _similarity(old_sigs.get(old_page, ""), new_sigs.get(guess, ""))
        both_blank = _is_blank(old_sigs.get(old_page, "")) and _is_blank(
            new_sigs.get(guess, "")
        )
        if score >= FALLBACK_THRESHOLD:
            pending.append((old_page, score, guess, "offset"))
        elif both_blank:
            # Cover / full-page figure: no text to compare, page number is all
            # we have, and the offset is book-wide.
            low_confidence.append(
                {
                    "old_page": old_page,
                    "new_page": guess,
                    "score": round(score, 3),
                    "offset": offset_hint,
                    "reason": "blank_page_text",
                }
            )
        else:
            low_confidence.append(
                {
                    "old_page": old_page,
                    "new_page": guess,
                    "score": round(score, 3),
                    "offset": offset_hint,
                    "reason": "low_text_similarity",
                }
            )

    # One old page per new page: highest score wins.
    matched: dict[int, dict[str, Any]] = {}
    for old_page, score, new_page, reason in sorted(pending, key=lambda x: -x[1]):
        if new_page in matched:
            unmatched.append(
                {
                    "old_page": old_page,
                    "best_new_page": new_page,
                    "best_score": round(score, 3),
                    "reason": "target_page_taken",
                }
            )
            continue
        matched[new_page] = {
            "old_page": old_page,
            "new_page": new_page,
            "score": round(score, 3),
            "offset": new_page - old_page,
            "match": reason,
        }
    low_confidence = [lc for lc in low_confidence if int(lc["new_page"]) not in matched]

    ordered = [matched[p] for p in sorted(matched)]
    low_confidence.sort(key=lambda x: int(x["old_page"]))
    unmatched.sort(key=lambda x: int(x["old_page"]))
    return ordered, low_confidence, unmatched, offset_hint


def _rekey_inherited(
    old_page_blocks: list[DocumentBlock],
    target_page: int,
    taken_ids: set[str],
) -> list[DocumentBlock]:
    out: list[DocumentBlock] = []
    for order, block in enumerate(old_page_blocks):
        new_id = block.id
        if new_id in taken_ids:
            new_id = f"{block.id}-inh{target_page}"
        meta = dict(block.meta or {})
        meta["inherited_from_page"] = block.page_no
        out.append(
            block.model_copy(
                update={
                    "id": new_id,
                    "page_no": target_page,
                    "order": order,
                    "meta": meta,
                }
            )
        )
    return out


def inherit_pages_from_job(
    session: Session,
    *,
    old_job_id: str,
    new_job_id: str,
    pages: list[int] | None = None,
    only_improved: bool = True,
    dry_run: bool = True,
    max_offset: int = DEFAULT_MAX_OFFSET,
    include_low_confidence: bool = False,
    commit: bool = True,
) -> dict[str, Any]:
    """Copy finished pages from ``old_job_id`` into ``new_job_id``.

    ``dry_run`` reports the alignment without touching the new job.
    ``include_low_confidence`` also carries pages aligned only by page offset
    (covers and other text-poor pages) — review the dry run first.
    ``commit=False`` leaves the transaction to the caller (import pipeline).
    """
    from app.services.corpus_import_incremental import upsert_page_draft_chunk
    from app.services.document_ir import render_page_html
    from app.services.import_ir_store import load_blocks, patch_job_ir_meta, save_blocks
    from app.services.ir.textbook_profile import default_profile_id

    if old_job_id == new_job_id:
        raise ValueError("old_job_id and new_job_id must differ")
    old_job = session.get(CorpusImportJob, old_job_id)
    if old_job is None:
        raise ValueError(f"source import job not found: {old_job_id}")
    new_job = session.get(CorpusImportJob, new_job_id)
    if new_job is None:
        raise ValueError(f"target import job not found: {new_job_id}")

    old_blocks = load_blocks(old_job) or []
    new_blocks = load_blocks(new_job) or []
    if not old_blocks:
        raise ValueError(f"source job has no IR blocks: {old_job_id}")
    if not new_blocks:
        raise ValueError(f"target job has no IR blocks: {new_job_id}")

    old_sigs = _signatures(old_blocks)
    new_sigs = _signatures(new_blocks)

    if pages:
        candidates = sorted({int(p) for p in pages if int(p) in old_sigs})
    elif only_improved:
        improved = improved_pages_for_job(session, old_job)
        candidates = [p for p in improved if p in old_sigs]
    else:
        candidates = sorted(old_sigs)

    matched, low_confidence, unmatched, offset_hint = _align_pages(
        old_sigs, new_sigs, candidates, max_offset=max_offset
    )
    if include_low_confidence:
        matched = sorted(matched + low_confidence, key=lambda m: int(m["new_page"]))

    old_by_page: dict[int, list[DocumentBlock]] = {}
    for block in old_blocks:
        old_by_page.setdefault(block.page_no, []).append(block)
    for page_blocks in old_by_page.values():
        page_blocks.sort(key=lambda b: (b.order, b.id))

    from app.services.corpus_corrections import page_replace_is_safe

    safe: list[dict[str, Any]] = []
    unsafe: list[dict[str, Any]] = []
    for item in matched:
        item["old_blocks"] = len(old_by_page.get(int(item["old_page"]), []))
        item["new_blocks"] = sum(
            1 for b in new_blocks if b.page_no == int(item["new_page"])
        )
        # The previous run is not uniformly better: some of its pages ended up
        # emptier than the fresh parse, and inheriting those would delete
        # content instead of restoring fixes.
        if page_replace_is_safe(item["old_blocks"], item["new_blocks"]):
            safe.append(item)
        else:
            unsafe.append({**item, "reason": "would_shrink_page"})
    matched = safe

    report: dict[str, Any] = {
        "old_job_id": old_job_id,
        "new_job_id": new_job_id,
        "dry_run": bool(dry_run),
        "only_improved": bool(only_improved),
        "candidate_pages": candidates,
        "old_page_span": max(old_sigs) if old_sigs else 0,
        "new_page_span": max(new_sigs) if new_sigs else 0,
        "offset_hint": offset_hint,
        "include_low_confidence": bool(include_low_confidence),
        "matched": matched,
        "skipped": unsafe,
        "low_confidence": [] if include_low_confidence else low_confidence,
        "unmatched": unmatched,
        "matched_count": len(matched),
        "skipped_count": len(unsafe),
        "low_confidence_count": 0 if include_low_confidence else len(low_confidence),
        "unmatched_count": len(unmatched),
    }
    if dry_run or not matched:
        report["applied_pages"] = []
        return report

    keep = [b for b in new_blocks if b.page_no not in {int(m["new_page"]) for m in matched}]
    taken_ids = {b.id for b in keep}
    rebuilt: list[DocumentBlock] = list(keep)
    for item in matched:
        target = int(item["new_page"])
        source_blocks = old_by_page.get(int(item["old_page"]), [])
        inherited = _rekey_inherited(source_blocks, target, taken_ids)
        taken_ids.update(b.id for b in inherited)
        rebuilt.extend(inherited)

    rebuilt.sort(key=lambda b: (b.page_no, b.order, b.id))
    blocks_key = save_blocks(new_job, rebuilt)
    patch_job_ir_meta(new_job, rebuilt, blocks_key)

    meta = loads(new_job.meta_json, {})
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    engine = str(meta.get("parse_engine") or "mineru_cloud")
    total_pages = int(meta.get("total_pages") or meta.get("ocr_total") or 0)

    applied_pages: list[int] = []
    for item in matched:
        target = int(item["new_page"])
        html = render_page_html(rebuilt, target, profile_id=profile_id)
        if not html.strip():
            continue
        upsert_page_draft_chunk(
            session,
            new_job,
            page_no=target,
            page_content=html,
            total_pages=total_pages,
            engine=engine,
            content_format="html",
            durable=False,
        )
        applied_pages.append(target)

    history = [h for h in (meta.get("page_inherit") or []) if isinstance(h, dict)]
    history.append(
        {
            "from_job_id": old_job_id,
            "pages": applied_pages,
            "offset_hint": offset_hint,
            "at": _now_iso(),
        }
    )
    meta["page_inherit"] = history[-20:]
    # Inherited pages carry an already-reviewed layout, so they no longer belong
    # in the escalate queue the agent works through.
    escalate = {int(p) for p in (meta.get("layout_escalate_pages") or [])}
    cleared = sorted(escalate & set(applied_pages))
    if cleared:
        meta["layout_escalate_pages"] = sorted(escalate - set(cleared))
        summary = dict(meta.get("layout_escalate_summary") or {})
        for page in cleared:
            summary.pop(str(page), None)
            summary.pop(page, None)  # type: ignore[arg-type]
        meta["layout_escalate_summary"] = summary
    new_job.meta_json = dumps(meta)
    session.add(new_job)
    session.flush()
    if commit:
        session.commit()

    logger.info(
        "page inherit done old=%s new=%s pages=%d offset=%s",
        old_job_id,
        new_job_id,
        len(applied_pages),
        offset_hint,
    )
    report["applied_pages"] = applied_pages
    report["applied_count"] = len(applied_pages)
    report["escalate_cleared"] = cleared
    return report
