"""Studio API auth — browser must send Studio token; gateway then uses admin token upstream."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings


def _expected_token() -> str:
    # Prefer dedicated STUDIO_TOKEN; fall back to admin token for lab setups.
    tok = (settings.studio_token or "").strip()
    if tok:
        return tok
    return (settings.buddy_admin_token or "").strip()


def extract_studio_token(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = (request.headers.get("x-studio-token") or "").strip()
    if header:
        return header
    # Same-origin <link>/<img> send cookies; EventSource may use query.
    cookie = (request.cookies.get("studio_token") or "").strip()
    if cookie:
        return cookie
    return (request.query_params.get("access_token") or "").strip()


class StudioAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path or ""
        if request.method == "OPTIONS":
            return await call_next(request)
        if not path.startswith("/api/v1/studio"):
            return await call_next(request)
        expected = _expected_token()
        if not expected:
            return JSONResponse(
                status_code=503,
                content={"detail": "Studio token 未配置（STUDIO_TOKEN / BUDDY_ADMIN_TOKEN）"},
            )
        got = extract_studio_token(request)
        if not got or got != expected:
            return JSONResponse(
                status_code=401,
                content={"detail": "需要 Studio 登录令牌（Authorization: Bearer <token>）"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
