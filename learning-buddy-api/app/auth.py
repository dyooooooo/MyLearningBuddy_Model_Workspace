from __future__ import annotations

import hashlib
import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_token(provided: str, expected: str) -> bool:
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided.strip(), expected.strip())


def extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key", "").strip()


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/", "/openapi.json", "/docs", "/redoc", "/metrics"):
            return await call_next(request)
        if path.startswith("/api/v1/system/health"):
            return await call_next(request)
        token = extract_bearer(request)
        if path.startswith("/api/v1/admin"):
            if not verify_token(token, settings.admin_token):
                raise HTTPException(status_code=401, detail="Invalid admin token")
            return await call_next(request)

        if not verify_token(token, settings.api_token):
            raise HTTPException(status_code=401, detail="Invalid API token")
        return await call_next(request)
