"""Companion pipeline REST API (/api/v1/companion)."""

from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response

from app.image_preprocess import read_normalized_upload_with_meta
from app.companion import service, store
from app.schemas import (
    AddFrameResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    FinalizePageResponse,
    HomeworkListResponse,
    MasteryListResponse,
    MasteryRecord,
    PatchMasteryRequest,
    SessionExportResponse,
)

router = APIRouter(prefix="/api/v1/companion", tags=["companion"])


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(body: CreateSessionRequest | None = None) -> CreateSessionResponse:
    body = body or CreateSessionRequest()
    session = service.create_session(subject=body.subject, note=body.note)
    return CreateSessionResponse(session=session)


@router.get("/sessions/{session_id}", response_model=CreateSessionResponse)
async def get_session(session_id: str) -> CreateSessionResponse:
    return CreateSessionResponse(session=service.get_session(session_id))


@router.post("/sessions/{session_id}/frames", response_model=AddFrameResponse)
async def add_frame(
    session_id: str,
    request: Request,
    file: Annotated[UploadFile, File(...)],
):
    already = request.headers.get("x-already-normalized", "").lower() in ("1", "true", "yes")
    data, mime, meta = await read_normalized_upload_with_meta(
        file, already_normalized=already
    )
    img_size = (int(meta["width"]), int(meta["height"]))
    return await service.add_frame(session_id, data, mime, img_size=img_size)


@router.get("/sessions/{session_id}/frames/{frame_id}/preview")
async def get_frame_preview(session_id: str, frame_id: str) -> Response:
    """Normalized JPEG saved at add-frame time (full resolution, not thumbnail)."""
    data = store.load_frame_preview(session_id, frame_id)
    return Response(content=data, media_type="image/jpeg")


@router.post("/sessions/{session_id}/finalize-page", response_model=FinalizePageResponse)
async def finalize_page(session_id: str) -> FinalizePageResponse:
    return service.finalize_page(session_id)


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
async def end_session(session_id: str) -> EndSessionResponse:
    return service.end_session(session_id)


@router.get("/sessions/{session_id}/export", response_model=SessionExportResponse)
async def export_session(session_id: str) -> SessionExportResponse:
    data = service.export_session(session_id)
    return SessionExportResponse.model_validate(data)


@router.get("/homework", response_model=HomeworkListResponse)
async def list_homework() -> HomeworkListResponse:
    return HomeworkListResponse(entries=store.list_homework())


@router.get("/homework/{entry_id}")
async def get_homework(entry_id: str):
    return store.get_homework(entry_id)


@router.get("/mastery", response_model=MasteryListResponse)
async def list_mastery() -> MasteryListResponse:
    return MasteryListResponse(records=store.list_mastery(), demo=True)


@router.patch("/mastery/{kp_id}", response_model=MasteryRecord)
async def patch_mastery(kp_id: str, body: PatchMasteryRequest) -> MasteryRecord:
    from datetime import datetime, timezone

    rec = store.get_mastery(kp_id)
    rec.mastery = body.mastery
    rec.updated_at = datetime.now(timezone.utc).isoformat()
    return store.upsert_mastery(rec)
