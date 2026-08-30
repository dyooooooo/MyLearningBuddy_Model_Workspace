"""Knowledge-base gateway: upload PDF, proxy buddy-api /admin/kb."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.buddy_client import corpus_admin_request
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/kb", tags=["kb"])


def _buddy_unavailable(exc: Exception) -> HTTPException:
    logger.error("buddy-api unreachable during kb request: %s", exc)
    return HTTPException(status_code=503, detail="知识库服务暂不可用，请稍后重试")


async def _buddy(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    content: bytes | None = None,
    extra_headers: dict | None = None,
) -> httpx.Response:
    return await corpus_admin_request(
        method,
        path,
        json=json,
        params=params,
        content=content,
        extra_headers=extra_headers,
    )


def _hint_or_empty(hint: str, label: str, filename: str) -> str:
    for raw in (hint, label, filename):
        text = (raw or "").strip()
        if text and not text.lower().endswith(".pdf"):
            return text
    return ""


@router.post("/import/pdf")
async def import_pdf(
    file: UploadFile | None = File(default=None),
    subject: str = Form("math"),
    grade_band: str = Form("hs"),
    category: str = Form("textbook"),
    label: str = Form(""),
    textbook_hint: str = Form(""),
    book_id: str = Form(""),
    conversation_id: str = Form(""),
) -> dict[str, Any]:
    existing_id = (book_id or "").strip()
    filename = (file.filename if file else "") or ""
    if existing_id:
        hint = _hint_or_empty(textbook_hint, label, filename)
        try:
            book = await _agent_patch_book(
                existing_id,
                subject=subject,
                grade_band=grade_band,
                category=category,
                label=label or filename,
                textbook_hint=hint,
                conversation_id=conversation_id,
                status="uploaded",
            )
        except httpx.ConnectError as exc:
            raise HTTPException(status_code=503, detail="教材 Agent 暂不可用") from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="教材 Agent 超时") from exc
        try:
            await _buddy("POST", f"/api/v1/admin/kb/convert/{book.get('id') or existing_id}")
        except httpx.ConnectError as exc:
            raise _buddy_unavailable(exc) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="知识库服务超时") from exc
        return book
    if not file or not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    book_id = uuid.uuid4().hex
    hint = _hint_or_empty(textbook_hint, label, filename)
    try:
        book = await _agent_upload_pdf(
            data,
            filename=filename,
            book_id=book_id,
            subject=subject,
            grade_band=grade_band,
            category=category,
            label=label or filename,
            textbook_hint=hint,
            conversation_id=conversation_id,
        )
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=503, detail="教材 Agent 暂不可用") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="教材 Agent 超时") from exc
    try:
        await _buddy("POST", f"/api/v1/admin/kb/convert/{book.get('id') or book_id}")
    except httpx.ConnectError as exc:
        raise _buddy_unavailable(exc) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="知识库服务超时") from exc
    return book


@router.post("/identify/{book_id}")
async def identify_preview(book_id: str) -> dict[str, Any]:
    sid = (book_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="id required")
    from app.buddy_client import _admin_headers

    url = f"{settings.buddy_api_url.rstrip('/')}/api/v1/admin/kb/identify/{sid}"
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            r = await client.post(url, headers=_admin_headers(), params={"pages": 3})
    except httpx.ConnectError as exc:
        raise _buddy_unavailable(exc) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="前三页识别超时") from exc
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=(r.text or "")[:400])
    out = r.json()
    if not isinstance(out, dict):
        raise HTTPException(status_code=502, detail="知识库服务返回异常")
    return out


@router.api_route("/settings/mineru", methods=["GET", "PUT"])
async def mineru_settings(request: Request) -> Response:
    body = await request.body()
    try:
        r = await _buddy(
            request.method,
            "/api/v1/admin/kb/settings/mineru",
            content=body if body else None,
            extra_headers={"content-type": request.headers.get("content-type", "application/json")}
            if body
            else None,
        )
    except httpx.ConnectError as exc:
        raise _buddy_unavailable(exc) from exc
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@router.post("/settings/mineru/test")
async def mineru_settings_test(request: Request) -> Response:
    body = await request.body()
    try:
        r = await _buddy(
            "POST",
            "/api/v1/admin/kb/settings/mineru/test",
            content=body if body else None,
            extra_headers={"content-type": request.headers.get("content-type", "application/json")}
            if body
            else None,
        )
    except httpx.ConnectError as exc:
        raise _buddy_unavailable(exc) from exc
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


def _agent_base() -> str:
    return (settings.textbook_agent_url or "").strip().rstrip("/")


def _agent_auth_headers() -> dict[str, str]:
    base = _agent_base()
    if not base:
        raise HTTPException(status_code=503, detail="教材 Agent 未配置")
    token = (settings.textbook_agent_token or "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="教材 Agent 令牌未配置")
    return {"Authorization": f"Bearer {token}"}


async def _agent_upload_pdf(
    data: bytes,
    *,
    filename: str,
    book_id: str,
    subject: str,
    grade_band: str,
    category: str = "textbook",
    label: str,
    textbook_hint: str,
    conversation_id: str = "",
) -> dict[str, Any]:
    headers = _agent_auth_headers()
    url = f"{_agent_base()}/v1/library"
    files = {"file": (filename, data, "application/pdf")}
    form = {
        "book_id": book_id,
        "subject": subject,
        "grade_band": grade_band,
        "category": category,
        "label": label,
        "textbook_hint": textbook_hint,
        "conversation_id": conversation_id,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.post(url, headers=headers, files=files, data=form)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"写入工作区失败：{r.text[:400]}")
    out = r.json()
    if not isinstance(out, dict):
        raise HTTPException(status_code=502, detail="教材 Agent 返回异常")
    return out


async def _agent_patch_book(
    book_id: str,
    *,
    subject: str,
    grade_band: str,
    category: str = "textbook",
    label: str,
    textbook_hint: str,
    conversation_id: str = "",
    status: str = "uploaded",
) -> dict[str, Any]:
    headers = {**_agent_auth_headers(), "Content-Type": "application/json"}
    url = f"{_agent_base()}/v1/library/{book_id}"
    body = {
        "subject": subject,
        "grade_band": grade_band,
        "category": category,
        "label": label,
        "textbook_hint": textbook_hint,
        "conversation_id": conversation_id,
        "status": status,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.patch(url, headers=headers, json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"更新书目失败：{r.text[:400]}")
    out = r.json()
    if not isinstance(out, dict):
        raise HTTPException(status_code=502, detail="教材 Agent 返回异常")
    return out


@router.api_route("/agent/{path:path}", methods=["GET", "PUT", "POST", "DELETE", "PATCH"])
async def proxy_agent(path: str, request: Request) -> Response:
    """Thin proxy to textbook-agent. No Cursor SDK in this process."""
    base = _agent_base()
    if not base:
        raise HTTPException(status_code=503, detail="教材 Agent 未配置")
    url = f"{base}/v1/{path}"
    headers = {
        **_agent_auth_headers(),
        "Accept": request.headers.get("accept") or "*/*",
    }
    ctype = request.headers.get("content-type")
    if ctype:
        headers["content-type"] = ctype
    body = await request.body()
    params = dict(request.query_params) if request.query_params else None
    stream = path == "events" or path.startswith("events?")
    try:
        if stream:
            client = httpx.AsyncClient(timeout=None)
            req = client.build_request(request.method, url, headers=headers, params=params, content=body or None)
            upstream = await client.send(req, stream=True)

            async def _iter():
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                finally:
                    await upstream.aclose()
                    await client.aclose()

            return StreamingResponse(
                _iter(),
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "text/event-stream"),
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        timeout = 300.0 if request.method == "POST" and path.split("/", 1)[0] == "library" else 60.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(
                request.method,
                url,
                headers=headers,
                params=params,
                content=body or None,
            )
    except httpx.ConnectError as exc:
        logger.error("textbook-agent unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="教材 Agent 暂不可用") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="教材 Agent 超时") from exc
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )


@router.api_route("/proxy/{path:path}", methods=["GET", "PUT", "POST", "DELETE", "PATCH"])
async def proxy_kb(path: str, request: Request) -> Response:
    if path == "books" or path.startswith("books/") or path.startswith("books?"):
        rest = path[5:].lstrip("/")
        agent_path = f"library/{rest}" if rest else "library"
        book_id = rest.split("/")[0] if rest else ""
        if request.method == "DELETE" and book_id and rest == book_id:
            try:
                await _buddy("DELETE", f"/api/v1/admin/kb/books/{book_id}")
            except httpx.ConnectError as exc:
                logger.error("kb purge after delete unreachable: %s", exc)
            except httpx.TimeoutException as exc:
                logger.error("kb purge after delete timeout: %s", exc)
        forwarded = await proxy_agent(agent_path, request)
        if (
            request.method == "POST"
            and path.endswith("/reimport")
            and forwarded.status_code < 400
        ):
            parts = path.split("/")
            book_id = parts[1] if len(parts) >= 2 else ""
            if book_id:
                try:
                    await _buddy("POST", f"/api/v1/admin/kb/convert/{book_id}")
                except httpx.ConnectError as exc:
                    raise _buddy_unavailable(exc) from exc
        return forwarded
    target = f"/api/v1/admin/kb/{path}"
    body = await request.body()
    try:
        r = await _buddy(
            request.method,
            target,
            params=dict(request.query_params) if request.query_params else None,
            content=body if body else None,
            extra_headers={"content-type": request.headers.get("content-type", "application/json")}
            if body
            else None,
        )
    except httpx.ConnectError as exc:
        raise _buddy_unavailable(exc) from exc
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
    )
