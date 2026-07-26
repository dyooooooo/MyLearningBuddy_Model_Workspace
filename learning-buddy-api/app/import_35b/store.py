"""Isolated persistence layer for the 35B layout import line.

This module is the ONLY writer/reader of ``import35b_jobs`` / ``import35b_pages``.
It shares nothing with the MinerU corpus tables (corpus_nodes / corpus_edges /
corpus_import_jobs) — no imports from ``corpus_import``, ``corpus_publish`` or
``corpus_import_incremental``.

Idempotency contract: a textbook is keyed by ``textbook_key`` (deterministic
from label+subject+grade_band). Re-importing the same textbook first purges all
its old pages (``delete_pages``) then rewrites page-by-page, so a shorter reimport
never leaves stale trailing pages behind.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import (
    Import35bCaseLaw,
    Import35bFeedback,
    Import35bJob,
    Import35bOverride,
    Import35bPage,
    Import35bReviewItem,
    Import35bSuppression,
)
from app.util.json_util import dumps, loads


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SLUG_RE = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")


def _slug(text: str) -> str:
    s = _SLUG_RE.sub("-", (text or "").strip()).strip("-")
    return s[:80]


def textbook_key_for(label: str, subject: str, grade_band: str) -> str:
    """Deterministic idempotency key for a textbook (same book -> same key).

    Includes a short stable hash so different labels that slug to the same
    ascii-stripped form still get distinct keys.
    """
    base = f"{subject}:{grade_band}:{label}".strip().lower()
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
    return f"tb35b:{subject}:{grade_band}:{_slug(label) or 'untitled'}:{digest}"


# --- jobs -----------------------------------------------------------------


def create_job(
    session: Session,
    *,
    minio_key: str,
    subject: str = "math",
    grade_band: str = "hs",
    label: str = "",
    profile_id: str = "hujiao_math_hs_v1",
    page_limit: int | None = None,
) -> Import35bJob:
    job = Import35bJob(
        id=str(uuid.uuid4()),
        textbook_key=textbook_key_for(label, subject, grade_band),
        label=label,
        subject=subject,
        grade_band=grade_band,
        minio_key=minio_key,
        status="uploaded",
        page_limit=page_limit,
        page_count=0,
        profile_id=profile_id,
        meta_json="{}",
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    session.add(job)
    session.flush()
    return job


def get_job(session: Session, job_id: str) -> Import35bJob | None:
    return session.get(Import35bJob, job_id)


def list_jobs(session: Session, limit: int = 50) -> list[Import35bJob]:
    return (
        session.query(Import35bJob)
        .order_by(Import35bJob.created_at.desc())
        .limit(limit)
        .all()
    )


def set_status(session: Session, job: Import35bJob, status: str, *, error: str = "") -> None:
    job.status = status
    if error:
        job.error = error[:4000]
    job.updated_at = _now_iso()
    session.flush()


def get_meta(job: Import35bJob) -> dict:
    return loads(job.meta_json, {})


def set_meta(session: Session, job: Import35bJob, meta: dict) -> None:
    job.meta_json = dumps(meta)
    job.updated_at = _now_iso()
    session.flush()


def patch_meta(session: Session, job: Import35bJob, patch: dict) -> None:
    meta = get_meta(job)
    meta.update(patch)
    set_meta(session, job, meta)


def delete_job(session: Session, job_id: str) -> bool:
    """Delete job row only. Prefer ``purge_job_data`` for full DB teardown."""
    job = session.get(Import35bJob, job_id)
    if job is None:
        return False
    session.delete(job)
    session.flush()
    return True


def purge_job_data(session: Session, job: Import35bJob) -> dict:
    """Remove all Postgres rows owned by this job (not suppressions — those are global)."""
    job_id = job.id
    textbook_key = job.textbook_key

    n_feedback = (
        session.query(Import35bFeedback)
        .filter(Import35bFeedback.job_id == job_id)
        .delete(synchronize_session=False)
    )

    n_review = (
        session.query(Import35bReviewItem)
        .filter(Import35bReviewItem.job_id == job_id)
        .delete(synchronize_session=False)
    )

    cases = (
        session.query(Import35bCaseLaw)
        .filter(Import35bCaseLaw.job_id == job_id)
        .all()
    )
    caselaw_ids = [c.id for c in cases]
    n_caselaw = (
        session.query(Import35bCaseLaw)
        .filter(Import35bCaseLaw.job_id == job_id)
        .delete(synchronize_session=False)
    )

    n_pages = (
        session.query(Import35bPage)
        .filter(
            Import35bPage.textbook_key == textbook_key,
            Import35bPage.job_id == job_id,
        )
        .delete(synchronize_session=False)
    )

    remaining_pages = (
        session.query(Import35bPage)
        .filter(Import35bPage.textbook_key == textbook_key)
        .count()
    )
    n_overrides = 0
    if remaining_pages == 0:
        n_overrides = (
            session.query(Import35bOverride)
            .filter(Import35bOverride.textbook_key == textbook_key)
            .delete(synchronize_session=False)
        )

    session.delete(job)
    session.flush()
    return {
        "feedback": int(n_feedback),
        "review_items": int(n_review),
        "caselaw": int(n_caselaw),
        "caselaw_ids": caselaw_ids,
        "pages": int(n_pages),
        "overrides": int(n_overrides),
    }


# --- user feedback (Cursor Repair Pack) ------------------------------------


def create_feedback(
    session: Session,
    *,
    job_id: str,
    page_no: int,
    textbook_key: str,
    label: str,
    body: str,
    tags: list[str] | None = None,
    block_id: str = "",
    screenshot_key: str = "",
    input_page_key: str = "",
    t1_snapshot: list[dict] | None = None,
    debug_prefix: str = "",
    prompt_version: str = "",
) -> Import35bFeedback:
    row = Import35bFeedback(
        id=str(uuid.uuid4()),
        job_id=job_id,
        page_no=page_no,
        textbook_key=textbook_key,
        label=label,
        body=body,
        tags_json=dumps(tags or []),
        block_id=block_id,
        screenshot_key=screenshot_key,
        input_page_key=input_page_key,
        t1_snapshot_json=dumps(t1_snapshot or []),
        debug_prefix=debug_prefix,
        prompt_version=prompt_version,
        status="open",
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    session.add(row)
    session.flush()
    return row


def get_feedback(session: Session, feedback_id: str) -> Import35bFeedback | None:
    return session.get(Import35bFeedback, feedback_id)


def list_feedback(
    session: Session,
    *,
    job_id: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[Import35bFeedback]:
    q = session.query(Import35bFeedback)
    if job_id:
        q = q.filter(Import35bFeedback.job_id == job_id)
    if status:
        q = q.filter(Import35bFeedback.status == status)
    return (
        q.order_by(Import35bFeedback.created_at.desc()).limit(limit).all()
    )


def set_feedback_status(
    session: Session,
    row: Import35bFeedback,
    *,
    status: str | None = None,
    pack_path: str | None = None,
    resolution_note: str | None = None,
) -> None:
    if status is not None:
        row.status = status
    if pack_path is not None:
        row.pack_path = pack_path
    if resolution_note is not None:
        row.resolution_note = resolution_note
    row.updated_at = _now_iso()
    session.flush()


def feedback_to_dict(row: Import35bFeedback) -> dict:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "page_no": row.page_no,
        "textbook_key": row.textbook_key,
        "label": row.label,
        "body": row.body,
        "tags": loads(row.tags_json, []),
        "block_id": row.block_id,
        "screenshot_key": row.screenshot_key,
        "input_page_key": row.input_page_key,
        "t1_snapshot": loads(row.t1_snapshot_json, []),
        "debug_prefix": row.debug_prefix,
        "prompt_version": row.prompt_version,
        "status": row.status,
        "pack_path": row.pack_path,
        "resolution_note": row.resolution_note,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


# --- pages ----------------------------------------------------------------


def delete_pages(session: Session, textbook_key: str) -> int:
    """Purge every page of a textbook — the idempotency reset before reimport."""
    n = (
        session.query(Import35bPage)
        .filter(Import35bPage.textbook_key == textbook_key)
        .delete(synchronize_session=False)
    )
    session.flush()
    return int(n)


def upsert_page(
    session: Session,
    *,
    textbook_key: str,
    job_id: str,
    page_no: int,
    html: str,
    plain_text: str = "",
    quality: dict | None = None,
) -> None:
    row = session.get(Import35bPage, (textbook_key, page_no))
    if row is None:
        row = Import35bPage(textbook_key=textbook_key, page_no=page_no)
        session.add(row)
    row.job_id = job_id
    row.html = html
    row.plain_text = plain_text
    row.quality_json = dumps(quality or {})
    row.updated_at = _now_iso()
    session.flush()


def get_pages(session: Session, textbook_key: str) -> list[Import35bPage]:
    return (
        session.query(Import35bPage)
        .filter(Import35bPage.textbook_key == textbook_key)
        .order_by(Import35bPage.page_no.asc())
        .all()
    )


def get_page(session: Session, textbook_key: str, page_no: int) -> Import35bPage | None:
    return session.get(Import35bPage, (textbook_key, page_no))


def count_pages(session: Session, textbook_key: str) -> int:
    return (
        session.query(Import35bPage)
        .filter(Import35bPage.textbook_key == textbook_key)
        .count()
    )


# --- v4 HITL: review items / suppressions / overrides ----------------------


def _suppressed(session: Session, finding: dict) -> bool:
    fp = finding.get("fingerprint") or ""
    fpe = finding.get("fingerprint_exact") or ""
    q = session.query(Import35bSuppression).filter(
        Import35bSuppression.fingerprint.in_([f for f in (fp, fpe) if f])
    )
    for sup in q.all():
        if sup.scope == "exact" and sup.fingerprint == fpe:
            return True
        if sup.scope == "fingerprint" and sup.fingerprint == fp:
            return True
    return False


def save_review_items(
    session: Session,
    *,
    job_id: str,
    textbook_key: str,
    page_no: int,
    findings: list[dict],
    replace_open: bool = True,
) -> int:
    """幂等写入一页的 finding：先清本页旧 open 项，再插入未被抑制的新项。

    ``replace_open=False`` 用于用户主动报告——追加而不清空规则 finding。
    """
    if replace_open:
        session.query(Import35bReviewItem).filter(
            Import35bReviewItem.textbook_key == textbook_key,
            Import35bReviewItem.page_no == page_no,
            Import35bReviewItem.status == "open",
        ).delete(synchronize_session=False)
    inserted = 0
    for f in findings:
        if _suppressed(session, f):
            continue
        session.add(
            Import35bReviewItem(
                id=str(uuid.uuid4()),
                job_id=job_id,
                textbook_key=textbook_key,
                page_no=page_no,
                block_id=str(f.get("block_id") or ""),
                suspect_type=str(f.get("suspect_type") or ""),
                severity=str(f.get("severity") or "medium"),
                evidence_json=dumps(f.get("evidence") or {}),
                candidates_json=dumps(f.get("candidates") or []),
                fingerprint=str(f.get("fingerprint") or ""),
                fingerprint_exact=str(f.get("fingerprint_exact") or ""),
                status="open",
                detector=str(f.get("detector") or "rule"),
                created_at=_now_iso(),
                updated_at=_now_iso(),
            )
        )
        inserted += 1
    session.flush()
    return inserted


def list_review_items(
    session: Session,
    *,
    job_id: str,
    status: str | None = None,
    limit: int = 500,
) -> list[Import35bReviewItem]:
    q = session.query(Import35bReviewItem).filter(Import35bReviewItem.job_id == job_id)
    if status:
        q = q.filter(Import35bReviewItem.status == status)
    return q.order_by(
        Import35bReviewItem.page_no.asc(), Import35bReviewItem.severity.asc()
    ).limit(limit).all()


def get_review_item(session: Session, review_id: str) -> Import35bReviewItem | None:
    return session.get(Import35bReviewItem, review_id)


def set_review_verdict(
    session: Session,
    item: Import35bReviewItem,
    *,
    status: str,
    verdict: dict,
) -> None:
    item.status = status
    item.verdict_json = dumps(verdict)
    item.updated_at = _now_iso()
    session.flush()


def add_suppression(
    session: Session,
    *,
    scope: str,
    fingerprint: str,
    suspect_type: str,
    reason: str = "",
) -> Import35bSuppression:
    sup = Import35bSuppression(
        id=str(uuid.uuid4()),
        scope=scope,
        fingerprint=fingerprint,
        suspect_type=suspect_type,
        reason=reason,
        created_at=_now_iso(),
    )
    session.add(sup)
    session.flush()
    return sup


def add_override(
    session: Session,
    *,
    textbook_key: str,
    page_no: int,
    block_id: str,
    target: str,
    payload: dict,
    review_item_id: str = "",
) -> Import35bOverride:
    row = Import35bOverride(
        id=str(uuid.uuid4()),
        textbook_key=textbook_key,
        page_no=page_no,
        block_id=block_id,
        target=target,
        payload_json=dumps(payload),
        review_item_id=review_item_id,
        created_at=_now_iso(),
    )
    session.add(row)
    session.flush()
    return row


def get_overrides(
    session: Session, textbook_key: str, page_no: int
) -> list[Import35bOverride]:
    return (
        session.query(Import35bOverride)
        .filter(
            Import35bOverride.textbook_key == textbook_key,
            Import35bOverride.page_no == page_no,
        )
        .order_by(Import35bOverride.created_at.asc())
        .all()
    )


# --- v4 判例库（canonical 落 Postgres，向量索引另在独立 Milvus collection）----


def add_caselaw(
    session: Session,
    *,
    kind: str,
    suspect_type: str,
    subject: str,
    grade_band: str,
    fingerprint: str,
    case_text: str,
    decision: dict,
    review_item_id: str = "",
    job_id: str = "",
    page_no: int = 0,
) -> Import35bCaseLaw:
    row = Import35bCaseLaw(
        id=str(uuid.uuid4()),
        kind=kind,
        suspect_type=suspect_type,
        subject=subject or "math",
        grade_band=grade_band or "hs",
        fingerprint=fingerprint,
        case_text=case_text,
        decision_json=dumps(decision or {}),
        review_item_id=review_item_id,
        job_id=job_id,
        page_no=page_no,
        vector_status="pending",
        created_at=_now_iso(),
    )
    session.add(row)
    session.flush()
    return row


def count_caselaw(session: Session, *, kind: str | None = None) -> int:
    q = session.query(Import35bCaseLaw)
    if kind:
        q = q.filter(Import35bCaseLaw.kind == kind)
    return q.count()


def list_caselaw(
    session: Session,
    *,
    kind: str | None = None,
    suspect_type: str | None = None,
    limit: int = 2000,
) -> list[Import35bCaseLaw]:
    q = session.query(Import35bCaseLaw)
    if kind:
        q = q.filter(Import35bCaseLaw.kind == kind)
    if suspect_type:
        q = q.filter(Import35bCaseLaw.suspect_type == suspect_type)
    return q.order_by(Import35bCaseLaw.created_at.asc()).limit(limit).all()


def set_caselaw_vector_status(
    session: Session, case_id: str, status: str
) -> None:
    row = session.get(Import35bCaseLaw, case_id)
    if row is not None:
        row.vector_status = status
        session.flush()
