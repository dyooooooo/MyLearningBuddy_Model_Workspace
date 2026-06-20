"""Companion pipeline REST API (/api/v1/companion)."""

from typing import Annotated
import re

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

import httpx

from app.image_preprocess import read_normalized_upload_with_meta

from app.companion import service, store
from app.buddy_client import ensure_student, list_students_admin, proxy_get, proxy_patch
from app.schemas import (
    AddFrameResponse,
    AddFramesBatchResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionResponse,
    FinalizePageResponse,
    HomeworkListResponse,
    HomeworkEntry,
    MasteryListResponse,
    MasteryRecord,
    PatchMasteryRequest,
    SessionExportResponse,
)

router = APIRouter(prefix="/api/v1/companion", tags=["companion"])


class EnsureStudentRequest(BaseModel):
    id: str = Field(..., min_length=3, max_length=32)
    label: str = ""
    grade_band: str = "hs"


_STUDENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")


@router.get("/buddy/students")
async def buddy_list_students():
    try:
        rows = await list_students_admin()
        return {
            "students": [{"id": r.get("id"), "label": r.get("label", "")} for r in rows],
            "count": len(rows),
            "max_students": None,
        }
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Buddy admin failed: {e.response.text}") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Buddy list-students failed: {e}") from e


@router.post("/buddy/ensure-student")
async def buddy_ensure_student(request: Request, body: EnsureStudentRequest):
    body.id = body.id.strip().lower()
    if body.label.strip():
        body.label = body.label.strip()
    else:
        body.label = body.id
    if not _STUDENT_ID_RE.match(body.id):
        raise HTTPException(
            status_code=400,
            detail="Invalid student id: lowercase letter first, 3–32 chars (a-z, 0-9, _, -). Example: bowen, student-01",
        )
    header_sid = request.headers.get("X-Student-Id", "").strip().lower()
    if header_sid and header_sid != body.id:
        raise HTTPException(status_code=400, detail="X-Student-Id must match body.id")
    try:
        return await ensure_student(body.id, label=body.label, grade_band=body.grade_band)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Buddy admin failed: {e.response.text}") from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Buddy ensure-student failed: {e}") from e


@router.get("/buddy/tier")
async def buddy_tier(request: Request):
    sid = _student_id(request)
    r = await proxy_get(f"/api/v1/students/{sid}/tier", sid)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Student not found; call ensure-student first")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Buddy tier failed")
    return r.json()


def _student_id(request: Request) -> str:
    sid = request.headers.get("X-Student-Id", "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="X-Student-Id header required")
    return sid


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(request: Request, body: CreateSessionRequest | None = None) -> CreateSessionResponse:
    body = body or CreateSessionRequest()
    sid = _student_id(request)
    session = service.create_session(student_id=sid, subject=body.subject, note=body.note)
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


@router.post("/sessions/{session_id}/frames/batch", response_model=AddFramesBatchResponse)
async def add_frames_batch(
    session_id: str,
    request: Request,
    files: Annotated[list[UploadFile], File(...)],
):
    if not files:
        raise HTTPException(status_code=400, detail="At least one file required")
    already = request.headers.get("x-already-normalized", "").lower() in ("1", "true", "yes")
    frame_inputs: list[tuple[bytes, str, tuple[int, int] | None]] = []
    for file in files:
        data, mime, meta = await read_normalized_upload_with_meta(
            file, already_normalized=already
        )
        img_size = (int(meta["width"]), int(meta["height"]))
        frame_inputs.append((data, mime, img_size))
    return await service.add_frames_batch_endpoint(session_id, frame_inputs)


@router.get("/sessions/{session_id}/frames/{frame_id}/preview")
async def get_frame_preview(session_id: str, frame_id: str) -> Response:
    session = store.load_session(session_id)
    sid = (session.meta or {}).get("student_id", "")
    data = store.load_frame_preview(str(sid), session_id, frame_id)
    return Response(content=data, media_type="image/jpeg")


@router.post("/sessions/{session_id}/finalize-page", response_model=FinalizePageResponse)
async def finalize_page(session_id: str) -> FinalizePageResponse:
    return await service.finalize_page(session_id)


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
async def end_session(session_id: str) -> EndSessionResponse:
    return await service.end_session(session_id)


@router.get("/sessions/{session_id}/export", response_model=SessionExportResponse)
async def export_session(session_id: str) -> SessionExportResponse:
    data = await service.export_session(session_id)
    return SessionExportResponse.model_validate(data)


@router.get("/homework", response_model=HomeworkListResponse)
async def list_homework(request: Request, session_id: str | None = None) -> HomeworkListResponse:
    sid = _student_id(request)
    r = await proxy_get(f"/api/v1/students/{sid}/homework", sid)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Buddy API homework list failed")
    entries_raw = r.json()
    if session_id:
        entries_raw = [e for e in entries_raw if e.get("session_id") == session_id]
    entries = [HomeworkEntry.model_validate(_buddy_homework_to_entry(e)) for e in entries_raw]
    return HomeworkListResponse(entries=entries)


@router.get("/homework/{entry_id}")
async def get_homework(request: Request, entry_id: str):
    sid = _student_id(request)
    r = await proxy_get(f"/api/v1/students/{sid}/homework/{entry_id}", sid)
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Homework not found")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Buddy API homework get failed")
    return JSONResponse(_buddy_homework_to_entry(r.json(), include_questions=True))


@router.get("/mastery", response_model=MasteryListResponse)
async def list_mastery(request: Request, session_id: str | None = None) -> MasteryListResponse:
    del session_id
    sid = _student_id(request)
    r = await proxy_get(f"/api/v1/students/{sid}/mastery", sid)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Buddy API mastery list failed")
    records = []
    for m in r.json():
        records.append(
            MasteryRecord(
                kp_id=m["kp_id"],
                label=m.get("label", m["kp_id"]),
                mastery=m.get("mastery", "unknown"),
                updated_at=m.get("updated_at", ""),
                demo=m.get("demo", False),
                inference_note=_mastery_note(m),
            )
        )
    records.sort(key=lambda rec: rec.kp_id)
    return MasteryListResponse(records=records, demo=False)


@router.patch("/mastery/{kp_id}", response_model=MasteryRecord)
async def patch_mastery(request: Request, kp_id: str, body: PatchMasteryRequest) -> MasteryRecord:
    sid = _student_id(request)
    r = await proxy_patch(
        f"/api/v1/students/{sid}/mastery/{kp_id}",
        {"mastery": body.mastery},
        sid,
    )
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail="Buddy mastery patch failed")
    m = r.json()
    return MasteryRecord(
        kp_id=m["kp_id"],
        label=m.get("label", kp_id),
        mastery=m.get("mastery", body.mastery),
        updated_at=m.get("updated_at", ""),
        demo=False,
        inference_note=_mastery_note(m),
    )


@router.get("/buddy/mastery")
async def buddy_mastery_raw(request: Request):
    sid = _student_id(request)
    r = await proxy_get(f"/api/v1/students/{sid}/mastery", sid)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Buddy API mastery failed")
    return r.json()


@router.get("/graph-runs")
async def list_graph_runs(request: Request):
    sid = _student_id(request)
    r = await proxy_get(f"/api/v1/students/{sid}/graph-runs", sid)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Buddy graph-runs failed")
    return r.json()


@router.get("/wrong-book")
async def wrong_book(request: Request):
    sid = _student_id(request)
    r = await proxy_get(f"/api/v1/students/{sid}/homework", sid)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Buddy homework list failed")
    items: list[dict] = []
    for hw in r.json():
        for q in hw.get("questions") or []:
            if q.get("is_wrong") is True:
                items.append(
                    {
                        "question_id": q.get("id"),
                        "homework_id": hw.get("id"),
                        "ordinal": q.get("ordinal"),
                        "print_text": q.get("print_text", ""),
                        "handwriting": q.get("handwriting", ""),
                        "kp_tags": q.get("kp_tags", []),
                        "finalized_at": hw.get("finalized_at"),
                    }
                )
    return {"items": items, "count": len(items)}


@router.patch("/questions/{question_id}")
async def patch_question(request: Request, question_id: str, body: dict):
    sid = _student_id(request)
    r = await proxy_patch(
        f"/api/v1/students/{sid}/questions/{question_id}",
        body,
        sid,
    )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Question not found")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Buddy question patch failed")
    return r.json()


def _mastery_note(m: dict) -> str:
    parts = []
    if m.get("weak"):
        parts.append("薄弱知识点")
    ev = m.get("evidence") or []
    if ev:
        parts.append(f"证据 {len(ev)} 条")
    return " · ".join(parts) if parts else "考纲 RAG 聚合"


def _buddy_homework_to_entry(raw: dict, include_questions: bool = False) -> dict:
    parts = []
    for q in raw.get("questions") or []:
        parts.append(
            {
                "kind": "question",
                "id": q.get("ordinal") or q.get("id"),
                "print": q.get("print_text", ""),
                "handwriting": q.get("handwriting", ""),
                "bbox_print": q.get("bbox_print", []),
                "bbox_handwriting": q.get("bbox_handwriting", []),
            }
        )
    out = {
        "id": raw["id"],
        "session_id": raw["session_id"],
        "page_id": raw["page_id"],
        "finalized_at": raw["finalized_at"],
        "source_frame_ids": raw.get("source_frame_ids", []),
        "parts": parts if parts else raw.get("parts", []),
        "meta": raw.get("meta", {}),
    }
    if include_questions:
        out["questions"] = raw.get("questions", [])
    return out
