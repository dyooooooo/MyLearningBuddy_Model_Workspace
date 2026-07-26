"""LAN OpenAI-compatible proxy: /v1/* → VLLM_BASE_URL (for WSL WTR etc.)."""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["openai-lan-proxy"])

_ALLOWED_PATHS = frozenset({"models", "chat/completions"})
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _check_lan_token(
    authorization: str | None,
    x_wtr_token: str | None,
    x_vllm_lan_token: str | None,
) -> None:
    if not settings.vllm_lan_proxy_enabled:
        raise HTTPException(404, "OpenAI LAN proxy disabled")
    expected = (settings.vllm_lan_token or "").strip()
    if not expected:
        raise HTTPException(503, "VLLM_LAN_TOKEN not configured")
    got = ""
    if x_wtr_token and x_wtr_token.strip():
        got = x_wtr_token.strip()
    elif x_vllm_lan_token and x_vllm_lan_token.strip():
        got = x_vllm_lan_token.strip()
    elif authorization:
        auth = authorization.strip()
        if auth.lower().startswith("bearer "):
            got = auth[7:].strip()
        else:
            got = auth
    if got != expected:
        raise HTTPException(401, "invalid LAN token")


def _upstream_url(path: str) -> str:
    base = settings.vllm_base_url.rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _filter_request_headers(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP:
            continue
        if lk in ("authorization", "x-wtr-token", "x-vllm-lan-token"):
            continue
        out[k] = v
    return out


@router.api_route("/models", methods=["GET"])
@router.api_route("/chat/completions", methods=["POST"])
async def proxy_openai(
    request: Request,
    authorization: str | None = Header(default=None),
    x_wtr_token: str | None = Header(default=None, alias="X-WTR-Token"),
    x_vllm_lan_token: str | None = Header(default=None, alias="X-VLLM-LAN-Token"),
):
    """Transparent OpenAI proxy for /v1/models and /v1/chat/completions."""
    _check_lan_token(authorization, x_wtr_token, x_vllm_lan_token)

    path = request.url.path
    # mounted at /v1 → path is /v1/models or /v1/chat/completions
    rel = path.removeprefix("/v1").lstrip("/")
    if rel not in _ALLOWED_PATHS:
        raise HTTPException(404, f"path not allowed: {rel}")

    url = _upstream_url(rel)
    timeout = httpx.Timeout(settings.vllm_lan_timeout_s, connect=30.0)
    headers = _filter_request_headers(request)
    body = await request.body()

    client = httpx.AsyncClient(timeout=timeout)
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                url,
                headers=headers,
                content=body if body else None,
                params=request.query_params,
            ),
            stream=True,
        )
    except httpx.RequestError as e:
        await client.aclose()
        logger.warning("vLLM LAN proxy upstream error: %s", e)
        raise HTTPException(502, f"vLLM upstream error: {e}") from e

    media = upstream.headers.get("content-type")
    out_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "content-type"
    }

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        gen(),
        status_code=upstream.status_code,
        media_type=media,
        headers=out_headers,
    )
