"""Corpus (MinerU) feedback store — isolated table from import35b_feedback."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import CorpusImportFeedback
from app.util.json_util import dumps, loads


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_feedback(
    session: Session,
    *,
    job_id: str,
    page_no: int,
    label: str,
    body: str,
    tags: list[str] | None = None,
    block_id: str = "",
    screenshot_key: str = "",
    input_page_key: str = "",
    t1_snapshot: list[dict] | None = None,
    debug_prefix: str = "",
) -> CorpusImportFeedback:
    row = CorpusImportFeedback(
        id=str(uuid.uuid4()),
        job_id=job_id,
        page_no=page_no,
        label=label,
        body=body,
        tags_json=dumps(tags or []),
        block_id=block_id,
        screenshot_key=screenshot_key,
        input_page_key=input_page_key,
        t1_snapshot_json=dumps(t1_snapshot or []),
        debug_prefix=debug_prefix,
        status="open",
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    session.add(row)
    session.flush()
    return row


def get_feedback(session: Session, feedback_id: str) -> CorpusImportFeedback | None:
    return session.get(CorpusImportFeedback, feedback_id)


def list_feedback(
    session: Session,
    *,
    job_id: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[CorpusImportFeedback]:
    q = session.query(CorpusImportFeedback)
    if job_id:
        q = q.filter(CorpusImportFeedback.job_id == job_id)
    if status:
        q = q.filter(CorpusImportFeedback.status == status)
    return (
        q.order_by(CorpusImportFeedback.created_at.desc()).limit(limit).all()
    )


def set_feedback_status(
    session: Session,
    row: CorpusImportFeedback,
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


def feedback_to_dict(row: CorpusImportFeedback) -> dict:
    return {
        "id": row.id,
        "job_id": row.job_id,
        "page_no": row.page_no,
        "label": row.label,
        "body": row.body,
        "tags": loads(row.tags_json, []),
        "block_id": row.block_id,
        "screenshot_key": row.screenshot_key,
        "input_page_key": row.input_page_key,
        "t1_snapshot": loads(row.t1_snapshot_json, []),
        "debug_prefix": row.debug_prefix,
        "status": row.status,
        "pack_path": row.pack_path,
        "resolution_note": row.resolution_note,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
