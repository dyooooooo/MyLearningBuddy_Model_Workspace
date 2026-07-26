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
    page_count = int(
        meta.get("total_pages")
        or meta.get("ocr_total")
        or max((b.page_no for b in blocks), default=0)
    )
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
        "matched": True,
    }
    job.meta_json = dumps(meta)
    session.add(job)
    return row


def find_matching_fingerprint(
    session: Session,
    job: CorpusImportJob,
    blocks: list[DocumentBlock],
) -> tuple[CorpusBookFingerprint | None, str]:
    """Return (fingerprint, match_kind) where match_kind is exact|soft|none."""
    feats = extract_book_features(job, blocks)
    exact = (
        session.query(CorpusBookFingerprint)
        .filter(CorpusBookFingerprint.fingerprint_hash == feats["fingerprint_hash"])
        .one_or_none()
    )
    if exact is not None:
        return exact, "exact"

    # Soft: same subject/grade/page_count + (toc OR first-page hash)
    q = (
        session.query(CorpusBookFingerprint)
        .filter(
            CorpusBookFingerprint.subject == feats["subject"],
            CorpusBookFingerprint.grade_band == feats["grade_band"],
            CorpusBookFingerprint.page_count == feats["page_count"],
        )
    )
    candidates = q.all()
    for row in candidates:
        if feats["toc_hash"] and row.toc_hash == feats["toc_hash"]:
            return row, "soft_toc"
        if feats["first_page_hash"] and row.first_page_hash == feats["first_page_hash"]:
            return row, "soft_first_page"
    return None, "none"


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
) -> CorpusCorrection:
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


def _block_from_dict(d: dict[str, Any]) -> DocumentBlock:
    return DocumentBlock.model_validate(d)


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

    corrections = list_corrections_for_fingerprint(session, fp.fingerprint_hash)
    if not corrections:
        # Link soft match into current row if hashes differ
        if current.fingerprint_hash != fp.fingerprint_hash:
            meta = loads(job.meta_json, {})
            meta["book_fingerprint"] = {
                "id": fp.id,
                "hash": fp.fingerprint_hash,
                "match": kind,
                "page_count": fp.page_count,
            }
            job.meta_json = dumps(meta)
        return {
            "applied": 0,
            "match": kind,
            "fingerprint_hash": fp.fingerprint_hash,
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
            by_id = {bid: b for bid, b in by_id.items() if bid in keep}
            for nb in new_page:
                by_id[nb.id] = nb
            pages_touched.add(page_no)
            applied += 1

    if not applied:
        return {
            "applied": 0,
            "match": kind,
            "fingerprint_hash": fp.fingerprint_hash,
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
    }
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
    }
