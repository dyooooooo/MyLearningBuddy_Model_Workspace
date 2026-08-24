"""Book fingerprint + correction replay for MinerU corpus imports.

Identity is content-based (page count, TOC titles, first-page text), not the
user-editable label — so a deleted/re-imported book can still match prior
35B re-recognize fixes.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CorpusBookFingerprint, CorpusCorrection, CorpusImportJob
from app.services.document_ir import DocumentBlock
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_CHAPTER_RE = re.compile(
    r"^(第[一二三四五六七八九十百零〇0-9]+[章节]|Chapter\s+\d+|目录|CONTENTS)",
    re.I,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _norm_text(text: str, *, limit: int = 800) -> str:
    t = _WS_RE.sub(" ", (text or "").strip())
    return t[:limit]


def _block_plain(block: DocumentBlock) -> str:
    return _norm_text(block.text or block.caption or "")


def extract_toc_titles(blocks: list[DocumentBlock], *, limit: int = 40) -> list[str]:
    """Collect distinctive TOC / chapter heading lines for fingerprinting."""
    titles: list[str] = []
    for b in sorted(blocks, key=lambda x: (x.page_no, x.order)):
        role = str((b.meta or {}).get("pedagogy_role") or "")
        mineru = str((b.meta or {}).get("mineru_type") or "")
        text = _block_plain(b)
        if not text or len(text) < 2:
            continue
        if (
            role in ("toc", "toc_item", "chapter_title", "section_title", "title")
            or mineru in ("index", "title", "list")
            or _CHAPTER_RE.search(text)
            or (b.type in ("heading", "title", "list") and b.page_no <= 12)
        ):
            titles.append(text[:120])
        if len(titles) >= limit:
            break
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in titles:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def extract_book_features(
    job: CorpusImportJob,
    blocks: list[DocumentBlock],
) -> dict[str, Any]:
    meta = loads(job.meta_json, {})
    page_span = int(max((b.page_no for b in blocks), default=0))
    meta_pages = int(meta.get("total_pages") or meta.get("ocr_total") or 0)
    # Identity follows the parsed IR span, not a stale meta counter: an early
    # partial parse could freeze total_pages (e.g. 20 for a 178-page book) and
    # split one book into two fingerprints that can never match each other.
    page_count = page_span or meta_pages
    toc = extract_toc_titles(blocks)
    toc_hash = _sha1("\n".join(toc)) if toc else ""

    first_page = [b for b in blocks if b.page_no == 1]
    first_blob = " ".join(_block_plain(b) for b in first_page if _block_plain(b))
    first_page_hash = _sha1(_norm_text(first_blob, limit=1200)) if first_blob else ""

    pdf_size = int(meta.get("pdf_bytes") or 0)

    subject = job.subject or "math"
    grade = job.grade_band or "hs"
    # Core identity string — label intentionally excluded.
    core = "|".join(
        [
            subject,
            grade,
            str(page_count),
            toc_hash,
            first_page_hash,
        ]
    )
    fingerprint_hash = _sha1(core)

    return {
        "fingerprint_hash": fingerprint_hash,
        "subject": subject,
        "grade_band": grade,
        "page_count": page_count,
        "page_span": page_span,
        "meta_page_count": meta_pages,
        "toc_titles": toc,
        "toc_hash": toc_hash,
        "first_page_hash": first_page_hash,
        "pdf_bytes_size": pdf_size,
        "label_hint": (job.label or "")[:512],
    }


def upsert_fingerprint(
    session: Session,
    job: CorpusImportJob,
    blocks: list[DocumentBlock],
) -> CorpusBookFingerprint:
    feats = extract_book_features(job, blocks)
    fp_hash = feats["fingerprint_hash"]
    row = (
        session.query(CorpusBookFingerprint)
        .filter(CorpusBookFingerprint.fingerprint_hash == fp_hash)
        .one_or_none()
    )
    now = _now_iso()
    if row is None:
        row = CorpusBookFingerprint(
            fingerprint_hash=fp_hash,
            subject=feats["subject"],
            grade_band=feats["grade_band"],
            page_count=feats["page_count"],
            toc_hash=feats["toc_hash"],
            first_page_hash=feats["first_page_hash"],
            pdf_bytes_size=feats["pdf_bytes_size"],
            label_hint=feats["label_hint"],
            features_json=dumps(feats),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.label_hint = feats["label_hint"] or row.label_hint
        row.pdf_bytes_size = feats["pdf_bytes_size"] or row.pdf_bytes_size
        row.features_json = dumps(feats)
        row.updated_at = now
    session.flush()

    meta = loads(job.meta_json, {})
    meta["book_fingerprint"] = {
        "id": row.id,
        "hash": row.fingerprint_hash,
        "page_count": row.page_count,
        "toc_hash": row.toc_hash,
        # "registered" only means this book's identity row exists; it says
        # nothing about matching a prior ledger (see apply_matched_corrections).
        "registered": True,
    }
    job.meta_json = dumps(meta)
    session.add(job)
    return row


# Re-parses of the same book rarely produce an identical page span (engine
# version, blank-page trimming). Allow a small window instead of demanding
# equality, which used to reject every real re-import.
_SOFT_PAGE_TOLERANCE_RATIO = 0.05
_SOFT_PAGE_TOLERANCE_MIN = 8


def soft_page_tolerance(page_count: int) -> int:
    return max(_SOFT_PAGE_TOLERANCE_MIN, int(round(abs(page_count) * _SOFT_PAGE_TOLERANCE_RATIO)))


def fingerprint_override_hash(job: CorpusImportJob) -> str:
    """Operator-declared book identity ("认亲") stored on the job meta."""
    meta = loads(job.meta_json, {})
    override = meta.get("book_fingerprint_override")
    if isinstance(override, dict):
        return str(override.get("hash") or "").strip()
    return str(override or "").strip()


def find_matching_fingerprint(
    session: Session,
    job: CorpusImportJob,
    blocks: list[DocumentBlock],
) -> tuple[CorpusBookFingerprint | None, str]:
    """Return (fingerprint, match_kind): override|exact|soft_*|none."""
    override = fingerprint_override_hash(job)
    if override:
        row = (
            session.query(CorpusBookFingerprint)
            .filter(CorpusBookFingerprint.fingerprint_hash == override)
            .one_or_none()
        )
        if row is not None:
            return row, "override"
        logger.warning(
            "book fingerprint override not found job=%s hash=%s", job.id, override
        )

    feats = extract_book_features(job, blocks)
    exact = (
        session.query(CorpusBookFingerprint)
        .filter(CorpusBookFingerprint.fingerprint_hash == feats["fingerprint_hash"])
        .one_or_none()
    )
    if exact is not None:
        return exact, "exact"

    # Soft: same subject/grade + (toc OR first-page hash) + page count in window
    candidates = (
        session.query(CorpusBookFingerprint)
        .filter(
            CorpusBookFingerprint.subject == feats["subject"],
            CorpusBookFingerprint.grade_band == feats["grade_band"],
        )
        .all()
    )
    pages = int(feats["page_count"])
    tolerance = soft_page_tolerance(pages)
    scored: list[tuple[int, CorpusBookFingerprint, str]] = []
    for row in candidates:
        delta = abs(int(row.page_count or 0) - pages)
        if delta > tolerance:
            continue
        if feats["toc_hash"] and row.toc_hash == feats["toc_hash"]:
            scored.append((delta, row, "soft_toc"))
        elif feats["first_page_hash"] and row.first_page_hash == feats["first_page_hash"]:
            scored.append((delta, row, "soft_first_page"))
    if scored:
        # Closest page span wins; toc evidence beats first-page evidence.
        scored.sort(key=lambda item: (item[0], 0 if item[2] == "soft_toc" else 1))
        _delta, row, kind = scored[0]
        return row, kind

    # Last tier: compare the *source PDFs*. Everything above is derived from the
    # IR, which layout rules and the agent rewrite — so two runs of one book
    # diverge there. The raw PDF text layer is untouched by any pipeline stage,
    # and the comparison skips cover/front matter and samples several body pages.
    row, kind = _match_by_source_pdf(session, job)
    if row is not None:
        return row, kind
    return None, "none"


def _match_by_source_pdf(
    session: Session, job: CorpusImportJob
) -> tuple[CorpusBookFingerprint | None, str]:
    from app.services.book_identity import match_job_by_source_pdf

    try:
        other, verdict = match_job_by_source_pdf(session, job)
    except Exception:  # noqa: BLE001 - identity fallback must never break import
        logger.warning("source-pdf identity match failed job=%s", job.id, exc_info=True)
        return None, "none"
    if other is None:
        return None, "none"
    other_meta = loads(other.meta_json, {})
    book = other_meta.get("book_fingerprint")
    fp_hash = str(book.get("hash") or "") if isinstance(book, dict) else ""
    if not fp_hash:
        return None, "none"
    row = (
        session.query(CorpusBookFingerprint)
        .filter(CorpusBookFingerprint.fingerprint_hash == fp_hash)
        .one_or_none()
    )
    if row is None:
        return None, "none"
    logger.info(
        "book identity via source pdf job=%s prior_job=%s kind=%s shared=%s similar=%s",
        job.id,
        other.id,
        verdict.get("kind"),
        verdict.get("shared_pages"),
        verdict.get("similar_pages"),
    )
    return row, f"soft_pdf_{verdict.get('kind') or 'match'}"


def save_correction(
    session: Session,
    *,
    job: CorpusImportJob,
    blocks: list[DocumentBlock],
    scope: str,
    page_no: int,
    block_id: str,
    user_note: str,
    before_blocks: list[dict[str, Any]],
    after_blocks: list[dict[str, Any]],
    refine_result: dict[str, Any] | None = None,
    ip_id: str = "",
    procedure: dict[str, Any] | None = None,
) -> CorpusCorrection:
    """Persist L3 page/block patch. Requires accepted IP id (no parallel ledger)."""
    if not (ip_id or "").strip():
        raise ValueError(
            "corpus_corrections require ip_id from an accepted Improvement Point; "
            "call improvement-points/confirm first"
        )
    matched, _kind = find_matching_fingerprint(session, job, blocks)
    current = upsert_fingerprint(session, job, blocks)
    # Prefer the matched prior book identity so soft-matched re-imports share one ledger.
    fp = matched or current
    now = _now_iso()
    # Append history (replay still takes latest per page/block via created_at).
    row = CorpusCorrection(
        fingerprint_id=fp.id,
        fingerprint_hash=fp.fingerprint_hash,
        scope=scope,
        page_no=page_no,
        block_id=block_id or "",
        user_note=user_note or "",
        before_json=dumps(before_blocks),
        after_json=dumps(after_blocks),
        refine_result_json=dumps(refine_result or {}),
        source_job_id=job.id,
        ip_id=ip_id or "",
        procedure_json=dumps(procedure or {}),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def list_corrections_for_fingerprint(
    session: Session, fingerprint_hash: str
) -> list[CorpusCorrection]:
    return (
        session.query(CorpusCorrection)
        .filter(CorpusCorrection.fingerprint_hash == fingerprint_hash)
        .order_by(CorpusCorrection.page_no.asc(), CorpusCorrection.created_at.asc())
        .all()
    )


def accepted_ip_ids(session: Session, fingerprint_hash: str) -> set[str]:
    from app.db.models import CorpusImprovementPoint

    return {
        row.id
        for row in session.query(CorpusImprovementPoint)
        .filter(
            CorpusImprovementPoint.fingerprint_hash == fingerprint_hash,
            CorpusImprovementPoint.status == "accepted",
        )
        .all()
    }


def replayable_corrections(
    session: Session, fingerprint_hash: str
) -> list[CorpusCorrection]:
    """Patches that would really apply: live accepted IP + non-empty after."""
    live = accepted_ip_ids(session, fingerprint_hash)
    out: list[CorpusCorrection] = []
    for corr in list_corrections_for_fingerprint(session, fingerprint_hash):
        if not (corr.ip_id or "") or corr.ip_id not in live:
            continue
        after = loads(corr.after_json, [])
        if not isinstance(after, list) or not after:
            continue
        out.append(corr)
    return out


def correction_ledger_stats(session: Session, fingerprint_hash: str) -> dict[str, Any]:
    """Ledger health for one book identity (used by adopt / inherit reports)."""
    rows = list_corrections_for_fingerprint(session, fingerprint_hash)
    live = accepted_ip_ids(session, fingerprint_hash)
    with_after = 0
    dangling = 0
    for corr in rows:
        after = loads(corr.after_json, [])
        if isinstance(after, list) and after:
            with_after += 1
        if not (corr.ip_id or "") or corr.ip_id not in live:
            dangling += 1
    replayable = replayable_corrections(session, fingerprint_hash)
    return {
        "fingerprint_hash": fingerprint_hash,
        "corrections": len(rows),
        "with_after": with_after,
        "dangling": dangling,
        "accepted_ips": len(live),
        "replayable": len(replayable),
        "replayable_pages": sorted({int(c.page_no or 0) for c in replayable}),
    }


def adopt_book_fingerprint(
    session: Session,
    job: CorpusImportJob,
    fingerprint_hash: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    """认亲: pin this import job to a prior book identity for L3 replay."""
    fp_hash = (fingerprint_hash or "").strip()
    if not fp_hash:
        raise ValueError("fingerprint_hash required")
    row = (
        session.query(CorpusBookFingerprint)
        .filter(CorpusBookFingerprint.fingerprint_hash == fp_hash)
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"unknown book fingerprint: {fp_hash}")

    meta = loads(job.meta_json, {})
    meta["book_fingerprint_override"] = {
        "id": row.id,
        "hash": row.fingerprint_hash,
        "page_count": row.page_count,
        "note": (note or "")[:500],
        "adopted_at": _now_iso(),
    }
    job.meta_json = dumps(meta)
    session.add(job)
    session.flush()
    return {
        "job_id": job.id,
        "adopted": meta["book_fingerprint_override"],
        "ledger": correction_ledger_stats(session, row.fingerprint_hash),
    }


def _block_from_dict(d: dict[str, Any]) -> DocumentBlock:
    return DocumentBlock.model_validate(d)


# A page-scope patch replaces the whole page. Older patches often stored only
# the one block the agent touched, so replaying them verbatim would delete the
# rest of the page.
PAGE_REPLACE_MIN_RATIO = 0.4
PAGE_REPLACE_GUARD_FLOOR = 4


def page_replace_is_safe(after_count: int, current_count: int) -> bool:
    if current_count < PAGE_REPLACE_GUARD_FLOOR:
        return True
    return after_count >= max(2, int(current_count * PAGE_REPLACE_MIN_RATIO))


def apply_matched_corrections(session: Session, job: CorpusImportJob) -> dict[str, Any]:
    """After a fresh import IR exists, replay stored corrections for this book."""
    from app.services.corpus_import_incremental import upsert_page_draft_chunk
    from app.services.document_ir import render_page_html
    from app.services.import_ir_store import load_blocks, patch_job_ir_meta, save_blocks
    from app.services.ir.textbook_profile import default_profile_id

    blocks = load_blocks(job) or []
    if not blocks:
        return {"applied": 0, "match": "none"}

    fp, kind = find_matching_fingerprint(session, job, blocks)
    # Always register current book's fingerprint (even if no prior corrections).
    current = upsert_fingerprint(session, job, blocks)
    if fp is None:
        return {
            "applied": 0,
            "match": kind,
            "fingerprint_hash": current.fingerprint_hash,
        }

    # Replay ONLY L3 rows linked to accepted IPs (legacy unlinked rows are abandoned).
    live_ips = accepted_ip_ids(session, fp.fingerprint_hash)
    corrections = [
        c
        for c in list_corrections_for_fingerprint(session, fp.fingerprint_hash)
        if (c.ip_id or "") and (c.ip_id or "") in live_ips
    ]

    from app.services.improvement_points import (
        get_book_edition_bind,
        procedures_for_fingerprint,
    )

    procedure_hints = procedures_for_fingerprint(session, fp.fingerprint_hash)
    bind = get_book_edition_bind(session, fp.fingerprint_hash)

    if not corrections:
        # Link soft match into current row if hashes differ
        meta = loads(job.meta_json, {})
        if current.fingerprint_hash != fp.fingerprint_hash:
            meta["book_fingerprint"] = {
                "id": fp.id,
                "hash": fp.fingerprint_hash,
                "match": kind,
                "page_count": fp.page_count,
            }
        if procedure_hints:
            meta["improvement_procedures"] = procedure_hints
        if bind:
            meta["bound_edition_id"] = bind.get("edition_id")
        job.meta_json = dumps(meta)
        session.flush()
        return {
            "applied": 0,
            "match": kind,
            "fingerprint_hash": fp.fingerprint_hash,
            "procedures": procedure_hints,
            "bound_edition_id": (bind or {}).get("edition_id") or "",
        }

    # Append history: replay only the latest correction per (scope, page, block).
    latest: dict[tuple[str, int, str], Any] = {}
    for corr in corrections:
        key = (corr.scope or "page", int(corr.page_no or 0), corr.block_id or "")
        prev = latest.get(key)
        if prev is None or str(corr.created_at or "") >= str(prev.created_at or ""):
            latest[key] = corr
    corrections = list(latest.values())
    corrections.sort(key=lambda c: (c.page_no, c.created_at or ""))

    by_id = {b.id: b for b in blocks}
    pages_touched: set[int] = set()
    skipped_pages: list[dict[str, Any]] = []
    applied = 0

    for corr in corrections:
        after = loads(corr.after_json, [])
        if not isinstance(after, list) or not after:
            continue
        if corr.scope == "block" and corr.block_id:
            # Replace single block if present in after payload
            for item in after:
                if not isinstance(item, dict):
                    continue
                try:
                    nb = _block_from_dict(item)
                except Exception:
                    continue
                if nb.id != corr.block_id and item.get("id") != corr.block_id:
                    continue
                nb = nb.model_copy(update={"page_no": corr.page_no or nb.page_no})
                by_id[nb.id] = nb
                pages_touched.add(nb.page_no)
                applied += 1
                break
        else:
            # Page scope: replace all blocks listed in after for that page
            page_no = int(corr.page_no)
            keep = {b.id for b in by_id.values() if b.page_no != page_no}
            new_page: list[DocumentBlock] = []
            for item in after:
                if not isinstance(item, dict):
                    continue
                try:
                    nb = _block_from_dict(item)
                except Exception:
                    continue
                if nb.page_no != page_no:
                    nb = nb.model_copy(update={"page_no": page_no})
                new_page.append(nb)
            if not new_page:
                continue
            current_count = sum(1 for b in by_id.values() if b.page_no == page_no)
            if not page_replace_is_safe(len(new_page), current_count):
                # A partial snapshot would wipe the rest of the page — the patch
                # is unusable as a whole-page replacement, so report it instead.
                skipped_pages.append(
                    {
                        "page_no": page_no,
                        "reason": "partial_page_snapshot",
                        "after_blocks": len(new_page),
                        "current_blocks": current_count,
                        "ip_id": corr.ip_id or "",
                        "note": (corr.user_note or "")[:120],
                    }
                )
                logger.warning(
                    "corpus correction skipped (partial page snapshot) job=%s page=%s "
                    "after=%d current=%d ip=%s",
                    job.id,
                    page_no,
                    len(new_page),
                    current_count,
                    corr.ip_id or "",
                )
                continue
            by_id = {bid: b for bid, b in by_id.items() if bid in keep}
            for nb in new_page:
                by_id[nb.id] = nb
            pages_touched.add(page_no)
            applied += 1

    if not applied:
        meta = loads(job.meta_json, {})
        meta["book_fingerprint"] = {
            "id": fp.id,
            "hash": fp.fingerprint_hash,
            "match": kind,
            "corrections_applied": 0,
            "skipped": skipped_pages[:50],
        }
        if procedure_hints:
            meta["improvement_procedures"] = procedure_hints
        if bind:
            meta["bound_edition_id"] = bind.get("edition_id")
        job.meta_json = dumps(meta)
        session.flush()
        return {
            "applied": 0,
            "match": kind,
            "fingerprint_hash": fp.fingerprint_hash,
            "skipped": skipped_pages,
            "procedures": procedure_hints,
            "bound_edition_id": (bind or {}).get("edition_id") or "",
        }

    new_blocks = sorted(by_id.values(), key=lambda b: (b.page_no, b.order, b.id))
    key = save_blocks(job, new_blocks)
    patch_job_ir_meta(job, new_blocks, key)

    meta = loads(job.meta_json, {})
    profile_id = str(meta.get("textbook_profile") or default_profile_id())
    engine = str(meta.get("parse_engine") or "mineru_cloud")
    total_pages = int(meta.get("total_pages") or meta.get("ocr_total") or 0)
    for page_no in sorted(pages_touched):
        html = render_page_html(new_blocks, page_no, profile_id=profile_id)
        if html.strip():
            upsert_page_draft_chunk(
                session,
                job,
                page_no=page_no,
                page_content=html,
                total_pages=total_pages,
                engine=engine,
                content_format="html",
                durable=False,
            )
    session.flush()
    # One commit so all replayed pages are durable before structure merge.
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    meta["book_fingerprint"] = {
        "id": fp.id,
        "hash": fp.fingerprint_hash,
        "match": kind,
        "corrections_applied": applied,
        "pages": sorted(pages_touched),
        "skipped": skipped_pages[:50],
    }
    if procedure_hints:
        meta["improvement_procedures"] = procedure_hints
    if bind:
        meta["bound_edition_id"] = bind.get("edition_id")
    job.meta_json = dumps(meta)
    session.add(job)
    session.flush()
    logger.info(
        "corpus corrections applied job=%s match=%s n=%d pages=%s",
        job.id,
        kind,
        applied,
        sorted(pages_touched),
    )
    return {
        "applied": applied,
        "match": kind,
        "fingerprint_hash": fp.fingerprint_hash,
        "pages": sorted(pages_touched),
        "skipped": skipped_pages,
        "procedures": procedure_hints,
        "bound_edition_id": (bind or {}).get("edition_id") or "",
    }
