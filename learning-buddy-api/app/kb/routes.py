from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from app.clients.redis_client import clear_kb_revoke, enqueue_kb_import
from app.kb import assets as kb_assets
from app.kb import import_service as svc
from app.kb.settings import (
    KbMineruConfigError,
    KbMineruOut,
    KbMineruTestOut,
    KbMineruTestRequest,
    KbMineruUpdate,
    get_settings_out,
    test_settings,
    update_settings,
)

router = APIRouter(prefix="/api/v1/admin/kb", tags=["kb-admin"])


class ConvertBody(BaseModel):
    book_id: str = ""


@router.get("/settings/mineru", response_model=KbMineruOut)
def get_kb_mineru() -> KbMineruOut:
    return get_settings_out()


@router.put("/settings/mineru", response_model=KbMineruOut)
def put_kb_mineru(body: KbMineruUpdate) -> KbMineruOut:
    try:
        return update_settings(body)
    except KbMineruConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/settings/mineru/test", response_model=KbMineruTestOut)
def test_kb_mineru(body: KbMineruTestRequest | None = None) -> KbMineruTestOut:
    return test_settings(body)


@router.get("/asset")
def get_asset(key: str = Query(..., min_length=1)) -> Response:
    try:
        normalized = kb_assets.assert_kb_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    data = kb_assets.read_bytes(normalized)
    if not data:
        raise HTTPException(status_code=404, detail="asset not found")
    return Response(content=data, media_type=kb_assets.content_type_for_name(normalized))


@router.delete("/books/{book_id}")
def delete_book(book_id: str) -> dict[str, Any]:
    sid = (book_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="id required")
    try:
        return svc.purge_kb_book(sid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/identify/{book_id}")
def identify_book(book_id: str, pages: int = Query(default=svc.IDENTIFY_PAGES, ge=1, le=20)) -> dict[str, Any]:
    sid = (book_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="id required")
    try:
        return svc.identify_kb_book(sid, pages=pages)
    except svc.KbBookGone as exc:
        raise HTTPException(status_code=404, detail="book not found") from exc
    except KbMineruConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)[:400]) from exc


@router.post("/convert/{book_id}")
def convert_book(book_id: str) -> dict[str, Any]:
    sid = (book_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="id required")
    clear_kb_revoke(sid)
    enqueue_kb_import(sid)
    return {"book_id": sid, "queued": True}


@router.post("/convert")
def convert_book_body(body: ConvertBody) -> dict[str, Any]:
    return convert_book(body.book_id)
