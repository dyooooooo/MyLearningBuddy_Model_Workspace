"""Recognise "this book was already imported" *before* running MinerU.

Two deliberate choices:

1. **Evidence comes from the raw PDF text layer**, never from the IR blocks.
   The IR is what layout rules and the agent rewrite, so two imports of the same
   book diverge there by design — comparing it measures our own edits, not the
   book. ``pdf_extract`` reads the PDF's own text, which no pipeline stage
   touches, so the same book always looks the same.

2. **Cover and front matter are skipped, and several body pages are sampled.**
   Page 1 is normally a cover image with almost no text (and covers of a series
   look alike), so a first-page comparison decides nothing. We fingerprint body
   pages instead, chosen by a content-anchored sketch (the lexicographically
   smallest page hashes) so the sample survives page-number drift between parses.
"""
from __future__ import annotations

import hashlib
import logging
import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import CorpusImportJob
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")

# Bump when the sampling policy changes: cached job signatures recompute.
SIGNATURE_VERSION = 2

# 封面 / 扉页 / 版权页：几乎没有正文，且同一套教材长得几乎一样 → 不作为证据。
SKIP_LEADING_PAGES = 3
# 取样页数（页哈希草图大小）；判同书至少要 MIN_SHARED_PAGES 页命中。
SAMPLE_PAGES = 8
MIN_SHARED_PAGES = 3
# 文字太少的页（整页插图、章节间隔页）不足以区分书。
MIN_SAMPLE_CHARS = 60
SAMPLE_TEXT_LIMIT = 500
SIMILAR_PAGE_THRESHOLD = 0.82

SOFT_PAGE_TOLERANCE_RATIO = 0.05
SOFT_PAGE_TOLERANCE_MIN = 6
CANDIDATE_JOB_LIMIT = 40
# Jobs that never produced usable pages cannot be inherited from.
INHERITABLE_STATUS = ("parsed", "chunking", "chunked", "review", "indexed")


def _sha1(data: bytes | str) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha1(raw).hexdigest()  # noqa: S324 - identity, not security


def _collapse(text: str) -> str:
    """Whitespace-collapsed text, for similarity scoring."""
    return _WS_RE.sub(" ", text or "").strip()


def _hash_key(text: str) -> str:
    """Whitespace-free text hash: CJK extractors differ on spacing, not glyphs."""
    return _sha1(_WS_RE.sub("", text or ""))


def _sample_pages(pages: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """Pick body pages by content, so the sample survives page-number drift.

    Position-anchored picks (e.g. "pages 10/30/50") shift when a parse trims
    blank pages; picking the smallest page hashes instead selects the *same
    pages* on both sides as long as those pages exist in both books.
    """
    eligible: list[dict[str, Any]] = []
    for page_no, text in pages:
        if page_no <= SKIP_LEADING_PAGES:
            continue
        collapsed = _collapse(text)
        if len(_WS_RE.sub("", collapsed)) < MIN_SAMPLE_CHARS:
            continue
        eligible.append(
            {
                "page": page_no,
                "hash": _hash_key(collapsed),
                "text": collapsed[:SAMPLE_TEXT_LIMIT],
            }
        )
    eligible.sort(key=lambda s: str(s["hash"]))
    picked = eligible[:SAMPLE_PAGES]
    picked.sort(key=lambda s: int(s["page"]))
    return picked


def pdf_signature(pdf_bytes: bytes, *, filename: str = "import.pdf") -> dict[str, Any]:
    """Identity of a raw PDF: exact digest plus a body-page text sketch."""
    from app.services.pdf_extract import extract_pdf_bytes

    sig: dict[str, Any] = {
        "v": SIGNATURE_VERSION,
        "sha1": _sha1(pdf_bytes),
        "size": len(pdf_bytes),
        "page_count": 0,
        "samples": [],
    }
    try:
        result = extract_pdf_bytes(pdf_bytes, filename=filename)
    except Exception:  # noqa: BLE001 - identity is best-effort, never blocking
        logger.warning("pdf signature extract failed filename=%s", filename, exc_info=True)
        return sig
    sig["page_count"] = int(result.meta.get("page_count") or 0)
    sig["samples"] = _sample_pages([(p.page_no, p.text or "") for p in result.pages])
    return sig


def source_pdf_signature(session: Session, job: CorpusImportJob) -> dict[str, Any]:
    """Signature of a previous job's source PDF, cached on the job meta."""
    meta = loads(job.meta_json, {})
    cached = meta.get("source_pdf")
    if isinstance(cached, dict) and int(cached.get("v") or 0) >= SIGNATURE_VERSION:
        # Includes the negative cache: a source PDF that could not be read is
        # remembered as unavailable, so a probe never re-waits on object storage.
        return {} if cached.get("unavailable") else cached
    if not job.minio_key:
        return {}
    from app.clients.minio_client import get_object_bytes

    raw: bytes | None = None
    try:
        raw = get_object_bytes(job.minio_key)
    except Exception:  # noqa: BLE001
        logger.warning("source pdf fetch failed job=%s key=%s", job.id, job.minio_key)
    if not raw:
        meta["source_pdf"] = {"v": SIGNATURE_VERSION, "unavailable": True}
        job.meta_json = dumps(meta)
        session.add(job)
        session.flush()
        return {}
    sig = pdf_signature(raw, filename=job.minio_key.rsplit("/", 1)[-1])
    meta["source_pdf"] = sig
    job.meta_json = dumps(meta)
    session.add(job)
    session.flush()
    return sig


def _page_tolerance(page_count: int) -> int:
    return max(SOFT_PAGE_TOLERANCE_MIN, int(round(abs(page_count) * SOFT_PAGE_TOLERANCE_RATIO)))


def _samples(sig: dict[str, Any]) -> list[dict[str, Any]]:
    raw = sig.get("samples")
    return [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []


def _similar_page_pairs(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> int:
    """Body pages that read almost the same — survives a different PDF export.

    ``quick_ratio`` is a cheap upper bound on ``ratio``, so it rejects unrelated
    pages in linear time; the quadratic diff only runs on plausible pairs. This
    matters because a probe compares one book against every prior import.
    """
    used: set[int] = set()
    hits = 0
    for left in a:
        text = str(left.get("text") or "")
        for idx, right in enumerate(b):
            if idx in used:
                continue
            matcher = SequenceMatcher(None, text, str(right.get("text") or ""))
            if matcher.real_quick_ratio() < SIMILAR_PAGE_THRESHOLD:
                continue
            if matcher.quick_ratio() < SIMILAR_PAGE_THRESHOLD:
                continue
            if matcher.ratio() < SIMILAR_PAGE_THRESHOLD:
                continue
            used.add(idx)
            hits += 1
            break
        if hits >= MIN_SHARED_PAGES:
            break
    return hits


def compare_signatures(incoming: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    """Decide whether two raw-PDF signatures describe the same book.

    kind: "" (different) | same_file | same_body_pages | similar_body_pages
    """
    out: dict[str, Any] = {"kind": "", "shared_pages": 0, "similar_pages": 0}
    if not prior:
        return out
    if incoming.get("sha1") and incoming["sha1"] == prior.get("sha1"):
        out["kind"] = "same_file"
        return out

    left, right = _samples(incoming), _samples(prior)
    if not left or not right:
        return out

    shared = {str(s["hash"]) for s in left} & {str(s["hash"]) for s in right}
    out["shared_pages"] = len(shared)
    if len(shared) >= MIN_SHARED_PAGES:
        # Identical body pages: no other book in the corpus can produce these,
        # so page-count drift does not matter here.
        out["kind"] = "same_body_pages"
        return out

    pages_in = int(incoming.get("page_count") or 0)
    pages_prior = int(prior.get("page_count") or 0)
    if not pages_in or not pages_prior:
        return out
    if abs(pages_in - pages_prior) > _page_tolerance(pages_in):
        return out
    similar = _similar_page_pairs(left, right)
    out["similar_pages"] = similar
    if similar >= MIN_SHARED_PAGES:
        # Different export/scan of the same book: text differs in spacing and
        # hyphenation, so hashes miss but the pages still read the same.
        out["kind"] = "similar_body_pages"
    return out


_MATCH_RANK = {"same_file": 0, "same_body_pages": 1, "similar_body_pages": 2}


def probe_known_books(
    session: Session,
    pdf_bytes: bytes,
    *,
    filename: str = "import.pdf",
    subject: str = "",
    grade_band: str = "",
) -> dict[str, Any]:
    """Previous imports of this same book, best candidate first.

    Used by the Studio import panel to offer "regenerate" vs "inherit last
    result" before any cloud parse is spent.
    """
    from app.services.corpus_corrections import correction_ledger_stats
    from app.services.corpus_page_inherit import improved_pages_for_job

    incoming = pdf_signature(pdf_bytes, filename=filename)

    q = session.query(CorpusImportJob).filter(
        CorpusImportJob.status.in_(INHERITABLE_STATUS)
    )
    if subject:
        q = q.filter(CorpusImportJob.subject == subject)
    if grade_band:
        q = q.filter(CorpusImportJob.grade_band == grade_band)
    jobs = q.order_by(CorpusImportJob.created_at.desc()).limit(CANDIDATE_JOB_LIMIT).all()

    candidates: list[dict[str, Any]] = []
    for job in jobs:
        prior = source_pdf_signature(session, job)
        verdict = compare_signatures(incoming, prior)
        if not verdict["kind"]:
            continue
        meta = loads(job.meta_json, {})
        fp = ""
        book = meta.get("book_fingerprint")
        if isinstance(book, dict):
            fp = str(book.get("hash") or "")
        improved = improved_pages_for_job(session, job)
        candidates.append(
            {
                "job_id": job.id,
                "label": job.label or "",
                "status": job.status,
                "edition_id": job.edition_id or "",
                "created_at": job.created_at,
                "match": verdict["kind"],
                "shared_pages": verdict["shared_pages"],
                "similar_pages": verdict["similar_pages"],
                "page_count": int(prior.get("page_count") or 0),
                "block_count": int(meta.get("block_count") or 0),
                "improved_pages": len(improved),
                "fingerprint_hash": fp,
                "ledger": correction_ledger_stats(session, fp) if fp else None,
            }
        )

    # Strongest evidence first, then the run with the most reviewed pages.
    candidates.sort(
        key=lambda c: (
            _MATCH_RANK.get(str(c["match"]), 9),
            -int(c["improved_pages"]),
            c["created_at"] or "",
        )
    )
    return {
        "signature": {k: v for k, v in incoming.items() if k != "samples"},
        "sample_pages": [int(s["page"]) for s in _samples(incoming)],
        "candidates": candidates,
        "best": candidates[0] if candidates else None,
    }


def match_job_by_source_pdf(
    session: Session, job: CorpusImportJob
) -> tuple[CorpusImportJob | None, dict[str, Any]]:
    """Previous job whose *source PDF* is the same book as ``job``'s.

    Used as the last identity tier during import: the IR-derived fingerprint can
    miss (rules and agent edits move the TOC and page text), while the source
    PDFs of the two runs are untouched.
    """
    sig = source_pdf_signature(session, job)
    if not sig:
        return None, {"kind": ""}
    rows = (
        session.query(CorpusImportJob)
        .filter(
            CorpusImportJob.id != job.id,
            CorpusImportJob.subject == job.subject,
            CorpusImportJob.grade_band == job.grade_band,
            CorpusImportJob.status.in_(INHERITABLE_STATUS),
        )
        .order_by(CorpusImportJob.created_at.desc())
        .limit(CANDIDATE_JOB_LIMIT)
        .all()
    )
    best: tuple[int, CorpusImportJob, dict[str, Any]] | None = None
    for other in rows:
        verdict = compare_signatures(sig, source_pdf_signature(session, other))
        kind = verdict["kind"]
        if not kind:
            continue
        rank = _MATCH_RANK.get(kind, 9)
        if best is None or rank < best[0]:
            best = (rank, other, verdict)
    if best is None:
        return None, {"kind": ""}
    return best[1], best[2]
