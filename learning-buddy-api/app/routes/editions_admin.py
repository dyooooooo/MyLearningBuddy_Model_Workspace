"""Edition Operations admin API — shared config, not MinerU/35B line-owned."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import db_session
from app.services.editions import ops as edition_ops

router = APIRouter(prefix="/api/v1/admin/editions", tags=["editions-admin"])


class DraftSaveBody(BaseModel):
    bundle: dict[str, Any]
    updated_by: str = ""


class PublishBody(BaseModel):
    version: str | None = None
    note: str = ""
    published_by: str = ""


class ForkBody(BaseModel):
    source_id: str = Field(..., min_length=1)
    new_id: str = Field(..., min_length=1)
    display_name: str = ""


class CorrectionEnqueueBody(BaseModel):
    edition_id: str
    candidate: dict[str, Any]
    source: str = "feedback"
    source_ref: str = ""


class CorrectionReviewBody(BaseModel):
    action: str  # approve|reject
    reviewer: str = ""
    review_note: str = ""


class CalibrateBody(BaseModel):
    edition_id: str = Field(..., min_length=1)
    display_name: str = ""
    sample_bboxes: list[list[float]] | None = None
    persist_fork: bool = False


@router.get("/overview")
def get_overview(session: Session = Depends(db_session)) -> dict:
    return edition_ops.overview(session)


@router.get("")
def list_editions(session: Session = Depends(db_session)) -> dict:
    return {"editions": edition_ops.list_editions(session)}


@router.get("/stats/jobs")
def job_stats(session: Session = Depends(db_session)) -> dict:
    return edition_ops.job_edition_stats(session)


@router.post("/fork")
def fork(body: ForkBody, session: Session = Depends(db_session)) -> dict:
    try:
        return edition_ops.fork_edition(
            session,
            source_id=body.source_id,
            new_id=body.new_id,
            display_name=body.display_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/corrections/queue")
def corrections_queue(
    edition_id: str | None = None,
    status: str | None = "pending",
    session: Session = Depends(db_session),
) -> dict:
    return {
        "items": edition_ops.list_corrections(
            session, edition_id=edition_id, status=status
        )
    }


@router.post("/corrections/enqueue")
def corrections_enqueue(
    body: CorrectionEnqueueBody,
    session: Session = Depends(db_session),
) -> dict:
    try:
        return edition_ops.enqueue_correction(
            session,
            edition_id=body.edition_id,
            candidate=body.candidate,
            source=body.source,
            source_ref=body.source_ref,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/corrections/{corr_id}/review")
def corrections_review(
    corr_id: str,
    body: CorrectionReviewBody,
    session: Session = Depends(db_session),
) -> dict:
    try:
        return edition_ops.review_correction(
            session,
            corr_id,
            action=body.action,
            reviewer=body.reviewer,
            review_note=body.review_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/calibrate")
def calibrate(body: CalibrateBody, session: Session = Depends(db_session)) -> dict:
    suggestion = edition_ops.suggest_calibration(
        edition_id=body.edition_id,
        display_name=body.display_name,
        sample_bboxes=body.sample_bboxes,
    )
    if body.persist_fork:
        try:
            edition_ops.fork_edition(
                session,
                source_id=suggestion["from"],
                new_id=body.edition_id,
                display_name=body.display_name,
            )
            edition_ops.save_draft(session, body.edition_id, suggestion["bundle"])
            for cand in suggestion.get("policy_candidates") or []:
                edition_ops.enqueue_correction(
                    session,
                    edition_id=body.edition_id,
                    candidate=cand,
                    source="calibration",
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        suggestion["persisted"] = True
    return suggestion


@router.get("/{edition_id}/bundle")
def get_bundle(edition_id: str, version: str | None = None) -> dict:
    try:
        return edition_ops.get_bundle_view(edition_id, version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{edition_id}/draft")
def get_draft(edition_id: str, session: Session = Depends(db_session)) -> dict:
    try:
        return edition_ops.get_or_create_draft(session, edition_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{edition_id}/draft")
def put_draft(
    edition_id: str,
    body: DraftSaveBody,
    session: Session = Depends(db_session),
) -> dict:
    try:
        return edition_ops.save_draft(
            session, edition_id, body.bundle, updated_by=body.updated_by
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{edition_id}/publish")
def publish(
    edition_id: str,
    body: PublishBody,
    session: Session = Depends(db_session),
) -> dict:
    try:
        return edition_ops.publish_draft(
            session,
            edition_id,
            version=body.version,
            note=body.note,
            published_by=body.published_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{edition_id}/rollback/{version}")
def rollback(
    edition_id: str,
    version: str,
    session: Session = Depends(db_session),
) -> dict:
    try:
        return edition_ops.rollback_version(session, edition_id, version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{edition_id}/versions")
def versions(edition_id: str, session: Session = Depends(db_session)) -> dict:
    return {"versions": edition_ops.list_versions(session, edition_id)}


@router.post("/{edition_id}/golden")
def golden(edition_id: str) -> dict:
    try:
        return edition_ops.run_golden_lite(edition_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
