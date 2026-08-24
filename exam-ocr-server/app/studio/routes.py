"""Corpus Studio proxy routes — upload PDF to MinIO, forward Admin API."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from app.buddy_client import corpus_admin_request
from app.storage.minio_client import put_import_bytes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/studio", tags=["studio"])


def _buddy_unavailable(exc: Exception) -> HTTPException:
    logger.error("buddy-api unreachable during studio import: %s", exc)
    return HTTPException(
        status_code=503,
        detail="导入服务暂不可用（buddy-api 未响应），请稍后重试",
    )


def _proxy_error_detail(status_code: int, body_text: str) -> Any:
    """Unwrap buddy-api ``{"detail": ...}`` so the UI does not see nested JSON."""
    text = (body_text or "").strip()
    if not text:
        return f"upstream error {status_code}"
    try:
        import json

        parsed = json.loads(text)
        if isinstance(parsed, dict) and "detail" in parsed:
            return parsed["detail"]
    except Exception:
        pass
    return text


# Two fully-isolated import lines, one Studio UI:
#   - mineru_cloud       → corpus line  (/proxy/*,   MinIO corpus/imports/)
#   - vision_layout_35b  → 35B line     (/proxy35b/*, MinIO import35b/)
_VALID_ENGINES = {"mineru_cloud", "vision_layout_35b"}
_ENGINE_35B = "vision_layout_35b"


@router.post("/import/pdf-probe")
async def probe_pdf(file: UploadFile = File(...)) -> dict[str, Any]:
    """Return basic PDF metadata (page count etc.) without creating an import job."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    # Forward raw PDF bytes to buddy-api probe endpoint (no multipart dependency).
    # 文件名必须 percent 编码：HTTP 头仅允许 latin-1，中文文件名会让 httpx 抛 UnicodeEncodeError
    try:
        r = await corpus_admin_request(
            "POST",
            "/api/v1/admin/corpus/import/pdf-probe",
            content=data,
            extra_headers={
                "content-type": "application/pdf",
                "x-filename": quote(file.filename or "upload.pdf", safe=""),
            },
        )
    except httpx.ConnectError as exc:
        raise _buddy_unavailable(exc) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="probe 超时") from exc
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"probe 失败：{r.text}")
    return r.json()


@router.post("/import/stage")
async def stage_pdf(file: UploadFile = File(...), line: str = Form("mineru")) -> dict[str, Any]:
    """预上传：把 PDF 传到 MinIO staging 前缀并返回真实 minio_key + 页数，不建导入任务。

    供「AI 方案」入口使用——Buddy Copilot 的 Agent 拿到真实 minio_key 后可
    渲染页面预览图（corpus.render_preview）并发起预览版导入（避免占位符 key）。
    双线隔离：line=mineru → corpus/imports/staging/；line=35b → import35b/staging/。
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    if line not in ("mineru", "35b"):
        raise HTTPException(status_code=400, detail="line 仅支持 mineru / 35b")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    prefix = "import35b/staging" if line == "35b" else "corpus/imports/staging"
    key = f"{prefix}/{uuid.uuid4().hex}/{file.filename}"
    put_import_bytes(key, data)
    logger.info("studio pdf staged key=%s bytes=%d", key, len(data))
    page_count: int | None = None
    try:
        r = await corpus_admin_request(
            "POST",
            "/api/v1/admin/corpus/import/pdf-probe",
            content=data,
            extra_headers={
                "content-type": "application/pdf",
                # HTTP 头仅允许 latin-1，中文文件名必须 percent 编码（buddy-api 侧 unquote）
                "x-filename": quote(file.filename or "upload.pdf", safe=""),
            },
        )
        if r.status_code < 400:
            page_count = r.json().get("page_count")
    except Exception:  # noqa: BLE001 - 探页只为补充页数，任何失败都不阻塞预上传
        logger.warning("stage probe failed (non-blocking)", exc_info=True)
    return {
        "minio_key": key,
        "filename": file.filename,
        "size": len(data),
        "page_count": page_count,
    }


@router.get("/import/object-stat")
async def import_object_stat(key: str) -> dict[str, Any]:
    """MinIO 对象预检（fail-fast）：仅支持两条导入线自己的前缀。"""
    if not (key.startswith("corpus/imports/") or key.startswith("import35b/")):
        raise HTTPException(status_code=400, detail="仅支持 corpus/imports/ 或 import35b/ 前缀")
    from app.storage.minio_client import get_minio
    from app.config import settings as _settings

    try:
        st = get_minio().stat_object(_settings.minio_bucket, key)
        return {"exists": True, "size": st.size, "content_type": st.content_type}
    except Exception:  # noqa: BLE001 - 不存在或 MinIO 异常统一返回 exists=False
        return {"exists": False}


@router.get("/import/object")
async def import_object_download(key: str) -> Response:
    """MinIO 对象下载：仅限两条导入线自己的前缀。

    供宿主机 buddy-copilot 拉取已预上传（staging）的 PDF，
    在本地渲染页面预览图给 Agent 逐页查看（页级排版方案的前置）。
    """
    if not (key.startswith("corpus/imports/") or key.startswith("import35b/")):
        raise HTTPException(status_code=400, detail="仅支持 corpus/imports/ 或 import35b/ 前缀")
    from app.storage.minio_client import get_minio
    from app.config import settings as _settings

    try:
        resp = get_minio().get_object(_settings.minio_bucket, key)
        try:
            data = resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"对象不存在或不可读：{key}") from exc
    media = "application/pdf" if key.lower().endswith(".pdf") else "application/octet-stream"
    return Response(content=data, media_type=media)


@router.post("/import/pdf")
async def import_pdf(
    file: UploadFile = File(...),
    subject: str = Form("math"),
    grade_band: str = Form("hs"),
    label: str = Form(""),
    textbook_id: str = Form(""),
    parse_engine: str = Form("mineru_cloud"),
    page_limit: int = Form(0),
    page_ranges: str = Form(""),
    edition_id: str = Form(""),
    profile_id: str = Form(""),
    inherit_from_job_id: str = Form(""),
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if parse_engine not in _VALID_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"parse_engine 无效：{parse_engine!r}。"
                f"有效值: {', '.join(sorted(_VALID_ENGINES))}"
            ),
        )
    eid = (edition_id or profile_id or "").strip()
    if not eid:
        raise HTTPException(
            status_code=400,
            detail="edition_id 必填（硬切：须显式选择教材方案包，如 hujiao_math_hs_v1）",
        )
    job_id = uuid.uuid4().hex
    is_35b = parse_engine == _ENGINE_35B
    # MinIO isolation at upload time: 35B PDFs land under import35b/, MinerU
    # PDFs under corpus/imports/. The two lines never share object prefixes.
    if is_35b:
        key = f"import35b/{job_id}/{file.filename}"
    else:
        key = f"corpus/imports/{job_id}/{file.filename}"
    logger.info(
        "studio pdf upload start filename=%s bytes=%d subject=%s grade=%s label=%r engine=%s page_limit=%s edition=%s line=%s",
        file.filename,
        len(data),
        subject,
        grade_band,
        label or file.filename,
        parse_engine,
        page_limit or None,
        eid,
        "35b" if is_35b else "mineru",
    )
    put_import_bytes(key, data, content_type="application/pdf")

    if is_35b:
        # Isolated 35B line — forward to the dedicated import35b API. No corpus
        # tree, no source_channel/textbook_id semantics.
        body_35b: dict[str, Any] = {
            "minio_key": key,
            "subject": subject,
            "grade_band": grade_band,
            "label": label or file.filename,
            "profile_id": eid,
        }
        if page_limit and page_limit > 0:
            body_35b["page_limit"] = page_limit
        try:
            r = await corpus_admin_request(
                "POST", "/api/v1/admin/import35b/jobs", json=body_35b
            )
        except httpx.ConnectError as exc:
            raise _buddy_unavailable(exc) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="导入服务超时，请稍后重试") from exc
        if r.status_code >= 400:
            logger.error("studio 35b import error status=%s body=%s", r.status_code, r.text[:500])
            raise HTTPException(status_code=502, detail=f"35B 导入失败：{r.text}")
        out = r.json()
        logger.info("studio 35b upload queued job_id=%s minio_key=%s", out.get("id"), key)
        return out

    body: dict[str, Any] = {
        "minio_key": key,
        "subject": subject,
        "grade_band": grade_band,
        "label": label or file.filename,
        "textbook_id": textbook_id,
        "source_channel": "pdf_import",
        "parse_engine": parse_engine,
        "edition_id": eid,
    }
    # 同一本书重导：继承上次已改好的页（35B 线不支持，仅 MinerU 线）
    inherit = (inherit_from_job_id or "").strip()
    if inherit:
        body["inherit_from_job_id"] = inherit
    pr = (page_ranges or "").strip()
    if pr:
        body["page_ranges"] = pr
    elif page_limit and page_limit > 0:
        body["page_limit"] = page_limit
    try:
        r = await corpus_admin_request("POST", "/api/v1/admin/corpus/import/pdf", json=body)
    except httpx.ConnectError as exc:
        raise _buddy_unavailable(exc) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="导入服务超时，请稍后重试") from exc
    if r.status_code >= 400:
        logger.error("studio pdf import buddy-api error status=%s body=%s", r.status_code, r.text[:500])
        raise HTTPException(status_code=502, detail=f"导入服务失败：{r.text}")
    out = r.json()
    logger.info("studio pdf upload queued job_id=%s minio_key=%s", out.get("id"), key)
    return out


@router.get("/settings/mineru")
async def get_mineru_settings() -> Any:
    r = await corpus_admin_request("GET", "/api/v1/admin/settings/mineru")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_proxy_error_detail(r.status_code, r.text))
    return r.json()


@router.put("/settings/mineru")
async def put_mineru_settings(request: Request) -> Any:
    body = await request.json()
    r = await corpus_admin_request("PUT", "/api/v1/admin/settings/mineru", json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_proxy_error_detail(r.status_code, r.text))
    return r.json()


@router.post("/settings/mineru/test")
async def test_mineru_settings(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception:
        body = {}
    r = await corpus_admin_request("POST", "/api/v1/admin/settings/mineru/test", json=body)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_proxy_error_detail(r.status_code, r.text))
    return r.json()


@router.get("/content-packs")
async def content_packs() -> Any:
    r = await corpus_admin_request("GET", "/api/v1/admin/content-packs")
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_proxy_error_detail(r.status_code, r.text))
    return r.json()


@router.api_route("/proxy-editions/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_editions(path: str, request: Request) -> Any:
    """Shared Edition Ops proxy → buddy-api ``/api/v1/admin/editions/*``.

    Neutral config plane — not owned by MinerU (/proxy) or 35B (/proxy35b).
    """
    sub = f"/api/v1/admin/editions/{path}" if path else "/api/v1/admin/editions"
    params = dict(request.query_params)
    body: dict | None = None
    content: bytes | None = None
    extra_headers: dict[str, str] | None = None
    if request.method in ("POST", "PATCH", "PUT"):
        content_type = (request.headers.get("content-type") or "").lower()
        if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            content = await request.body()
            extra_headers = {"Content-Type": request.headers.get("content-type", "")}
        else:
            try:
                body = await request.json()
            except Exception:
                body = None
    r = await corpus_admin_request(
        request.method,
        sub,
        json=body,
        content=content,
        params=params if params else None,
        extra_headers=extra_headers,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_proxy_error_detail(r.status_code, r.text))
    if r.status_code == 204:
        return {}
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


@router.api_route("/proxy35b/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def proxy_import35b(path: str, request: Request) -> Any:
    """Isolated 35B-line proxy → buddy-api ``/admin/import35b/*``.

    Physically separate from ``/proxy/*`` (corpus): the two lines share no proxy
    path, no MinIO prefix, no buddy-api route namespace, no DB table.
    """
    sub = f"/api/v1/admin/import35b/{path}"
    params = dict(request.query_params)
    body: dict | None = None
    content: bytes | None = None
    extra_headers: dict[str, str] | None = None
    if request.method in ("POST", "PATCH", "PUT"):
        content_type = (request.headers.get("content-type") or "").lower()
        if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            content = await request.body()
            extra_headers = {"Content-Type": request.headers.get("content-type", "")}
        else:
            try:
                body = await request.json()
            except Exception:
                body = None
    r = await corpus_admin_request(
        request.method,
        sub,
        json=body,
        content=content,
        params=params if params else None,
        extra_headers=extra_headers,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_proxy_error_detail(r.status_code, r.text))
    if r.status_code == 204:
        return {}
    content_type = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if (
        content_type.startswith("image/")
        or content_type.startswith("text/")
        or content_type == "application/octet-stream"
    ):
        headers = {}
        if content_type.startswith("text/css"):
            headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        return Response(
            content=r.content,
            media_type=content_type or "application/octet-stream",
            status_code=r.status_code,
            headers=headers,
        )
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


@router.post("/copilot/cascade-by-import-job/{job_id}")
async def cascade_delete_copilot_by_job(job_id: str) -> dict:
    """Explicit cascade: delete Copilot sessions bound to an import job."""
    try:
        client = _copilot_client_get()
        from app.config import settings as _settings

        r = await client.request(
            "DELETE",
            f"/api/v1/copilot/sessions/by-import-job/{job_id}",
            headers={"Authorization": f"Bearer {_settings.buddy_admin_token}"},
        )
        if r.status_code >= 400:
            raise HTTPException(
                status_code=r.status_code,
                detail=_proxy_error_detail(r.status_code, r.text),
            )
        try:
            return r.json()
        except Exception:
            return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("cascade delete copilot sessions for job %s failed: %s", job_id, exc)
        raise HTTPException(status_code=503, detail="cascade delete failed") from exc


@router.api_route("/proxy/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"])
async def proxy_corpus(path: str, request: Request) -> Any:
    """Forward Corpus Studio API calls to buddy-api admin corpus routes."""
    # Legacy markdown used /proxy/corpus/import/asset (double "corpus" segment).
    if path.startswith("corpus/"):
        path = path[len("corpus/") :]
    sub = f"/api/v1/admin/corpus/{path}"
    params = dict(request.query_params)

    # SSE: stream import job events without buffering
    if request.method == "GET" and path.endswith("/events"):
        from starlette.background import BackgroundTask
        from starlette.responses import StreamingResponse

        from app.buddy_client import _admin_headers, _client_get

        client = _client_get()
        req = client.build_request(
            "GET",
            sub,
            params=params or None,
            headers=_admin_headers(),
            timeout=httpx.Timeout(None, connect=5.0),
        )
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            logger.error("buddy-api unreachable (import sse): %s", exc)
            raise HTTPException(status_code=503, detail="导入事件流不可用") from exc
        if upstream.status_code >= 400:
            text = (await upstream.aread()).decode("utf-8", "replace")
            await upstream.aclose()
            raise HTTPException(
                status_code=upstream.status_code,
                detail=_proxy_error_detail(upstream.status_code, text),
            )
        return StreamingResponse(
            upstream.aiter_raw(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            background=BackgroundTask(upstream.aclose),
        )

    body = None
    if request.method in ("POST", "PATCH", "PUT"):
        try:
            body = await request.json()
        except Exception:
            body = None
    r = await corpus_admin_request(
        request.method,
        sub,
        json=body,
        params=params if params else None,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_proxy_error_detail(r.status_code, r.text))

    # 删除导入任务 → 显式级联删除 Copilot（不再只靠路径正则副作用）
    if request.method == "DELETE":
        m = re.match(r"^import/jobs/([^/]+)/?$", path)
        if m:
            job_id = m.group(1)
            try:
                await cascade_delete_copilot_by_job(job_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "cascade delete copilot sessions for job %s failed: %s",
                    job_id,
                    exc,
                )

    if r.status_code == 204:
        return {}
    content_type = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    # Pass through binary and non-JSON text payloads (e.g. text/css stylesheet)
    # with their original content-type so the browser applies them correctly.
    if (
        content_type.startswith("image/")
        or content_type.startswith("text/")
        or content_type == "application/octet-stream"
    ):
        headers = {}
        if content_type.startswith("text/css"):
            headers = {
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        return Response(
            content=r.content,
            media_type=content_type or "application/octet-stream",
            status_code=r.status_code,
            headers=headers,
        )
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


# ---------------------------------------------------------------------------
# Buddy Copilot（宿主机 Cursor SDK 编排服务）代理
# ---------------------------------------------------------------------------

_copilot_client: httpx.AsyncClient | None = None


def _copilot_client_get() -> httpx.AsyncClient:
    global _copilot_client
    if _copilot_client is None:
        from app.config import settings

        _copilot_client = httpx.AsyncClient(
            base_url=settings.copilot_base_url.rstrip("/"),
            timeout=httpx.Timeout(120.0, connect=5.0),
        )
    return _copilot_client


@router.api_route("/copilot/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_copilot(path: str, request: Request) -> Any:
    """Studio ↔ Buddy Copilot（:8200，宿主机）。

    Copilot 只绑宿主机回环地址；本代理是它对 LAN 的唯一入口。
    - `.../events`：SSE（Server-Sent Events）流式透传（实时消息推送）
    - multipart（附件上传）：原始 body 透传
    - 其余：JSON 请求/响应
    """
    sub = f"/api/v1/copilot/{path}" if path else "/api/v1/copilot"
    params = dict(request.query_params)
    client = _copilot_client_get()
    from app.config import settings as _settings

    auth = {"Authorization": f"Bearer {_settings.buddy_admin_token}"}

    # SSE 事件流：流式透传，不缓冲
    if request.method == "GET" and path.endswith("/events"):
        from starlette.background import BackgroundTask
        from starlette.responses import StreamingResponse

        req = client.build_request(
            "GET", sub, params=params or None, headers=auth, timeout=httpx.Timeout(None, connect=5.0)
        )
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            logger.error("buddy-copilot unreachable (sse): %s", exc)
            raise HTTPException(
                status_code=503,
                detail="Copilot 服务未启动（宿主机 :8200），请先运行 buddy-copilot/run.sh",
            )
        if upstream.status_code >= 400:
            text = (await upstream.aread()).decode("utf-8", "replace")
            await upstream.aclose()
            raise HTTPException(status_code=upstream.status_code, detail=_proxy_error_detail(upstream.status_code, text))
        return StreamingResponse(
            upstream.aiter_raw(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            background=BackgroundTask(upstream.aclose),
        )

    body: dict | None = None
    content: bytes | None = None
    headers = dict(auth)
    ctype = request.headers.get("content-type", "")
    if request.method == "POST":
        if ctype.startswith("multipart/"):
            content = await request.body()
            headers["Content-Type"] = ctype
        else:
            try:
                body = await request.json()
            except Exception:
                body = None
    try:
        r = await client.request(
            request.method,
            sub,
            json=body,
            content=content,
            params=params if params else None,
            headers=headers,
        )
    except httpx.HTTPError as exc:
        logger.error("buddy-copilot unreachable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Copilot 服务未启动（宿主机 :8200），请先运行 buddy-copilot/run.sh",
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=_proxy_error_detail(r.status_code, r.text))
    if r.status_code == 204:
        return {}
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}
