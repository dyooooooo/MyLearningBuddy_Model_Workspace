"""Finalize one import as *the* version of a textbook, and drop the rest.

A book gets imported several times while its layout is being tuned. Once an
operator is satisfied with one run, keeping the others is pure liability: they
show up in the tree, they get picked as inheritance sources, and their stale
improvement points can be replayed onto future imports at the wrong pages.

"定稿" therefore does two things:

1. Locks the chosen job (``meta.finalized``) so neither the job nor its textbook
   subtree can be deleted by the ordinary delete paths.
2. Removes every *other* import of the same book — subtree and vectors, page
   feedback, improvement points and patches, book fingerprints, object storage
   objects, book-specific Edition forks, and the job row itself.

Same-book detection reuses the two identity layers: the IR-derived book
fingerprint, and the raw-PDF signature (``book_identity``) which still matches
after rules and the agent have rewritten the pages.

Everything runs as a preview first (``dry_run``). The preview carries warnings —
most importantly when a version about to be deleted holds *more* review work
than the one being kept — and applying while warnings stand requires an explicit
acknowledgement.
"""
from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    CorpusBookEditionBind,
    CorpusBookFingerprint,
    CorpusCorrection,
    CorpusImportFeedback,
    CorpusImportJob,
    CorpusImprovementPoint,
    Edition,
    EditionCorrectionQueue,
    EditionDraft,
    EditionVersion,
)
from app.util.json_util import dumps, loads

logger = logging.getLogger(__name__)

# A version worth keeping has produced pages; anything earlier is still running.
FINALIZE_READY_STATUS = ("parsed", "chunking", "chunked", "review", "indexed")
BASE_EDITION_ID = "_base"
# corpus/imports/<job>/ and corpus/imports/staging/<hex>/ are per-import; the
# levels above them are shared, so a purge must never climb that high.
MIN_PREFIX_DEPTH = 3


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def job_fingerprint_hash(job: CorpusImportJob) -> str:
    meta = loads(job.meta_json, {})
    book = meta.get("book_fingerprint")
    if isinstance(book, dict):
        return str(book.get("hash") or "").strip()
    return ""


def finalized_record(job: CorpusImportJob) -> dict[str, Any]:
    meta = loads(job.meta_json, {})
    rec = meta.get("finalized")
    return rec if isinstance(rec, dict) else {}


def is_finalized(job: CorpusImportJob) -> bool:
    return bool(finalized_record(job).get("at"))


def finalized_job_for_node(session: Session, node_ids: list[str]) -> CorpusImportJob | None:
    """The finalized job whose textbook lives in ``node_ids``, if any."""
    if not node_ids:
        return None
    rows = (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.textbook_node_id.in_(list(node_ids)))
        .all()
    )
    return next((j for j in rows if is_finalized(j)), None)


def owned_minio_prefix(job: CorpusImportJob) -> str:
    """Object-storage folder this import owns (source PDF + assets + debug)."""
    key = (job.minio_key or "").strip().lstrip("/")
    if not key:
        return ""
    parent = PurePosixPath(key).parent
    parts = [p for p in parent.parts if p]
    if len(parts) < MIN_PREFIX_DEPTH:
        return ""
    return "/".join(parts) + "/"


def prefix_still_in_use(session: Session, prefix: str, *, exclude_job_id: str) -> bool:
    """Does another import still own objects under ``prefix``?

    Two imports can be started from the same staged PDF folder, so a purge for
    one of them must not take the other's source PDF with it.
    """
    normalized = (prefix or "").strip()
    if not normalized:
        return False
    rows = (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.id != exclude_job_id)
        .all()
    )
    for other in rows:
        other_prefix = owned_minio_prefix(other)
        if not other_prefix:
            continue
        if other_prefix == normalized or other_prefix.startswith(normalized):
            return True
    return False


def same_book_jobs(
    session: Session, job: CorpusImportJob
) -> list[tuple[CorpusImportJob, str]]:
    """Other imports of the same book, each with the evidence that matched it."""
    from app.services.book_identity import compare_signatures, source_pdf_signature

    fp = job_fingerprint_hash(job)
    rows = (
        session.query(CorpusImportJob)
        .filter(CorpusImportJob.id != job.id)
        .order_by(CorpusImportJob.created_at.desc())
        .all()
    )
    own_sig: dict[str, Any] | None = None
    out: list[tuple[CorpusImportJob, str]] = []
    for other in rows:
        if fp and job_fingerprint_hash(other) == fp:
            out.append((other, "fingerprint"))
            continue
        if other.subject != job.subject or other.grade_band != job.grade_band:
            continue
        if own_sig is None:
            own_sig = source_pdf_signature(session, job)
        if not own_sig:
            continue
        verdict = compare_signatures(own_sig, source_pdf_signature(session, other))
        if verdict["kind"]:
            out.append((other, verdict["kind"]))
    return out


def _ledger_counts(session: Session, fingerprint_hash: str, job_id: str) -> dict[str, int]:
    ip_q = session.query(CorpusImprovementPoint)
    corr_q = session.query(CorpusCorrection)
    if fingerprint_hash:
        ip_q = ip_q.filter(
            (CorpusImprovementPoint.fingerprint_hash == fingerprint_hash)
            | (CorpusImprovementPoint.source_job_id == job_id)
        )
        corr_q = corr_q.filter(
            (CorpusCorrection.fingerprint_hash == fingerprint_hash)
            | (CorpusCorrection.source_job_id == job_id)
        )
    else:
        ip_q = ip_q.filter(CorpusImprovementPoint.source_job_id == job_id)
        corr_q = corr_q.filter(CorpusCorrection.source_job_id == job_id)
    return {"improvement_points": ip_q.count(), "corrections": corr_q.count()}


def _subtree_size(session: Session, node_id: str) -> int:
    if not node_id:
        return 0
    from app.services.corpus_tree import collect_subtree_ids

    try:
        ids = collect_subtree_ids(session, node_id)
    except Exception:  # noqa: BLE001 - counting is advisory
        return 0
    return len(set(ids) | {node_id})


def _editions_to_drop(
    session: Session,
    *,
    keep_job: CorpusImportJob,
    removed_job_ids: set[str],
    removed_fingerprints: set[str],
) -> list[dict[str, Any]]:
    """Book-specific Edition forks left behind by the deleted versions.

    Only forks nobody else references are dropped: the base bundle and any
    edition still used by a surviving import stay, because the finalized book
    needs its layout rules at runtime.
    """
    candidates: set[str] = set()
    for bind in (
        session.query(CorpusBookEditionBind)
        .filter(CorpusBookEditionBind.fingerprint_hash.in_(list(removed_fingerprints) or [""]))
        .all()
    ):
        if bind.edition_id:
            candidates.add(bind.edition_id)
    for job in session.query(CorpusImportJob).filter(
        CorpusImportJob.id.in_(list(removed_job_ids) or [""])
    ):
        if job.edition_id:
            candidates.add(job.edition_id)

    candidates.discard(BASE_EDITION_ID)
    candidates.discard(keep_job.edition_id or "")

    survivors_using: set[str] = set()
    for job in session.query(CorpusImportJob).all():
        if job.id in removed_job_ids or not job.edition_id:
            continue
        survivors_using.add(job.edition_id)
    for bind in session.query(CorpusBookEditionBind).all():
        if bind.fingerprint_hash in removed_fingerprints:
            continue
        if bind.edition_id:
            survivors_using.add(bind.edition_id)

    out: list[dict[str, Any]] = []
    for eid in sorted(candidates - survivors_using):
        row = session.get(Edition, eid)
        out.append(
            {
                "edition_id": eid,
                "display_name": (row.display_name if row else "") or eid,
                "base_id": (row.base_id if row else "") or "",
                "in_catalog": row is not None,
                "config_dir": f"configs/editions/{eid}",
            }
        )
    return out


def preview_finalize(session: Session, job_id: str) -> dict[str, Any]:
    """What 定稿 would keep and delete, plus the warnings worth stopping for."""
    keep = session.get(CorpusImportJob, job_id)
    if keep is None:
        raise ValueError(f"import job not found: {job_id}")

    keep_fp = job_fingerprint_hash(keep)
    keep_ledger = _ledger_counts(session, keep_fp, keep.id)
    keep_prefix = owned_minio_prefix(keep)

    warnings: list[str] = []
    if keep.status not in FINALIZE_READY_STATUS:
        warnings.append(
            f"这版状态是 {keep.status}，还没解析出可用页面；确认要把它定为唯一版本吗"
        )
    if not keep_fp:
        warnings.append("这版没有书指纹（可能没跑完解析），同书判定只能靠源 PDF")

    removed: list[dict[str, Any]] = []
    removed_fingerprints: set[str] = set()
    for other, why in same_book_jobs(session, keep):
        other_fp = job_fingerprint_hash(other)
        ledger = _ledger_counts(session, other_fp, other.id)
        prefix = owned_minio_prefix(other)
        # Two imports can share a staged PDF folder; never purge the kept one's.
        if prefix and keep_prefix and (prefix == keep_prefix or keep_prefix.startswith(prefix)):
            prefix = ""
        entry = {
            "job_id": other.id,
            "label": other.label or "",
            "status": other.status,
            "created_at": other.created_at,
            "match": why,
            "edition_id": other.edition_id or "",
            "fingerprint_hash": other_fp,
            "textbook_node_id": other.textbook_node_id or "",
            "node_count": _subtree_size(session, other.textbook_node_id or ""),
            "feedback_count": session.query(CorpusImportFeedback)
            .filter(CorpusImportFeedback.job_id == other.id)
            .count(),
            "minio_prefix": prefix,
            "finalized": is_finalized(other),
            **ledger,
        }
        removed.append(entry)
        if other_fp and other_fp != keep_fp:
            removed_fingerprints.add(other_fp)
        if entry["finalized"]:
            warnings.append(
                f"任务 {other.id[:8]}（{other.label or '无标签'}）已经定稿过，"
                "继续会把它连同数据一起删掉"
            )
        if why == "similar_body_pages":
            warnings.append(
                f"任务 {other.id[:8]}（{other.label or '无标签'}）只是「正文相似」，"
                "不是同一份文件；如果它其实是同套书的另一册，请先取消"
            )
        if int(entry["improvement_points"]) > int(keep_ledger["improvement_points"]):
            warnings.append(
                f"要删的 {other.id[:8]} 改进点比保留的这版多"
                f"（{entry['improvement_points']} vs {keep_ledger['improvement_points']}）—— "
                "确认定稿的是改进最全的那一版吗"
            )

    editions = _editions_to_drop(
        session,
        keep_job=keep,
        removed_job_ids={r["job_id"] for r in removed},
        removed_fingerprints=removed_fingerprints,
    )

    return {
        "keep": {
            "job_id": keep.id,
            "label": keep.label or "",
            "status": keep.status,
            "edition_id": keep.edition_id or "",
            "fingerprint_hash": keep_fp,
            "textbook_node_id": keep.textbook_node_id or "",
            "node_count": _subtree_size(session, keep.textbook_node_id or ""),
            "already_finalized": is_finalized(keep),
            **keep_ledger,
        },
        "remove": removed,
        "remove_count": len(removed),
        "remove_fingerprints": sorted(removed_fingerprints),
        "editions_to_drop": editions,
        "warnings": warnings,
    }


def _purge_fingerprint_ledger(session: Session, fingerprint_hash: str) -> dict[str, int]:
    """Delete a dead book's improvement points, patches and identity row."""
    from app.services.improvement_points import purge_improvement_point

    counts = {"improvement_points": 0, "corrections": 0, "fingerprints": 0, "binds": 0}
    ip_ids = [
        row.id
        for row in session.query(CorpusImprovementPoint)
        .filter(CorpusImprovementPoint.fingerprint_hash == fingerprint_hash)
        .all()
    ]
    for ip_id in ip_ids:
        try:
            purge_improvement_point(session, ip_id)
            counts["improvement_points"] += 1
        except Exception:  # noqa: BLE001 - keep going; report what was removed
            logger.warning("purge ip failed id=%s", ip_id, exc_info=True)
    counts["corrections"] += (
        session.query(CorpusCorrection)
        .filter(CorpusCorrection.fingerprint_hash == fingerprint_hash)
        .delete(synchronize_session=False)
    )
    counts["binds"] += (
        session.query(CorpusBookEditionBind)
        .filter(CorpusBookEditionBind.fingerprint_hash == fingerprint_hash)
        .delete(synchronize_session=False)
    )
    counts["fingerprints"] += (
        session.query(CorpusBookFingerprint)
        .filter(CorpusBookFingerprint.fingerprint_hash == fingerprint_hash)
        .delete(synchronize_session=False)
    )
    return counts


def _drop_edition(session: Session, edition_id: str) -> dict[str, int]:
    counts = {"drafts": 0, "versions": 0, "queue": 0, "catalog": 0}
    counts["drafts"] = (
        session.query(EditionDraft)
        .filter(EditionDraft.edition_id == edition_id)
        .delete(synchronize_session=False)
    )
    counts["versions"] = (
        session.query(EditionVersion)
        .filter(EditionVersion.edition_id == edition_id)
        .delete(synchronize_session=False)
    )
    counts["queue"] = (
        session.query(EditionCorrectionQueue)
        .filter(EditionCorrectionQueue.edition_id == edition_id)
        .delete(synchronize_session=False)
    )
    row = session.get(Edition, edition_id)
    if row is not None:
        session.delete(row)
        counts["catalog"] = 1
    return counts


def finalize_book(
    session: Session,
    job_id: str,
    *,
    note: str = "",
    ack_warnings: bool = False,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Keep ``job_id`` as the only version of its book; delete the others."""
    report = preview_finalize(session, job_id)
    report["dry_run"] = bool(dry_run)
    report["applied"] = False
    if dry_run:
        return report
    if report["warnings"] and not ack_warnings:
        raise ValueError(
            "定稿有告警未确认：" + "；".join(report["warnings"]) + "。确认后重试（ack_warnings）"
        )

    from app.clients.minio_client import delete_objects_by_prefix
    from app.services.corpus_admin import delete_import_job, delete_node

    keep = session.get(CorpusImportJob, job_id)
    if keep is None:
        raise ValueError(f"import job not found: {job_id}")

    totals = {
        "jobs": 0,
        "nodes": 0,
        "feedback": 0,
        "improvement_points": 0,
        "corrections": 0,
        "fingerprints": 0,
        "binds": 0,
        "objects": 0,
        "editions": 0,
    }

    # Ledger first: it is keyed by fingerprint, so it must go before the jobs
    # that carry those fingerprints disappear.
    for fp in report["remove_fingerprints"]:
        counts = _purge_fingerprint_ledger(session, fp)
        for key, value in counts.items():
            totals[key] += value
    session.flush()
    session.commit()

    for entry in report["remove"]:
        loser_id = str(entry["job_id"])
        node_id = str(entry["textbook_node_id"] or "")
        if node_id and node_id != (keep.textbook_node_id or ""):
            try:
                out = delete_node(session, node_id, cascade=True)
                totals["nodes"] += int(out.get("count") or 0)
            except Exception:  # noqa: BLE001 - one stuck subtree must not abort all
                logger.exception("finalize: subtree delete failed node=%s", node_id)
        totals["feedback"] += (
            session.query(CorpusImportFeedback)
            .filter(CorpusImportFeedback.job_id == loser_id)
            .delete(synchronize_session=False)
        )
        # Leftovers keyed by job only (their fingerprint may equal the kept one).
        totals["corrections"] += (
            session.query(CorpusCorrection)
            .filter(
                CorpusCorrection.source_job_id == loser_id,
                CorpusCorrection.fingerprint_hash != report["keep"]["fingerprint_hash"],
            )
            .delete(synchronize_session=False)
        )
        session.flush()
        session.commit()

        prefix = str(entry["minio_prefix"] or "")
        if prefix:
            totals["objects"] += delete_objects_by_prefix(prefix)
        try:
            delete_import_job(session, loser_id, force=True)
            totals["jobs"] += 1
        except Exception:  # noqa: BLE001
            logger.exception("finalize: job delete failed id=%s", loser_id)

    for entry in report["editions_to_drop"]:
        counts = _drop_edition(session, str(entry["edition_id"]))
        totals["editions"] += 1 if counts["catalog"] else 0
    session.flush()
    session.commit()

    keep = session.get(CorpusImportJob, job_id)
    if keep is None:
        raise ValueError(f"import job vanished during finalize: {job_id}")
    meta = loads(keep.meta_json, {})
    meta["finalized"] = {
        "at": _now_iso(),
        "note": note,
        "removed_jobs": [str(e["job_id"]) for e in report["remove"]],
        "removed_fingerprints": report["remove_fingerprints"],
        "dropped_editions": [str(e["edition_id"]) for e in report["editions_to_drop"]],
        "totals": totals,
    }
    keep.meta_json = dumps(meta)
    keep.updated_at = _now_iso()
    session.add(keep)
    session.flush()
    session.commit()

    logger.info(
        "book finalized job=%s removed_jobs=%d nodes=%d ips=%d objects=%d editions=%d",
        job_id,
        totals["jobs"],
        totals["nodes"],
        totals["improvement_points"],
        totals["objects"],
        totals["editions"],
    )
    report["applied"] = True
    report["totals"] = totals
    report["finalized"] = meta["finalized"]
    return report


def unfinalize_book(session: Session, job_id: str) -> dict[str, Any]:
    """Release the delete lock. Deleted versions are gone — this only unlocks."""
    job = session.get(CorpusImportJob, job_id)
    if job is None:
        raise ValueError(f"import job not found: {job_id}")
    meta = loads(job.meta_json, {})
    previous = meta.pop("finalized", None)
    job.meta_json = dumps(meta)
    job.updated_at = _now_iso()
    session.add(job)
    session.flush()
    session.commit()
    return {"job_id": job_id, "unlocked": bool(previous), "was": previous or {}}
