from __future__ import annotations

from fastapi import Request

from app.rag.kp_resolver import resolve_kp_label


def locale_from_request(request: Request | None, *, default: str = "zh") -> str:
    if request is None:
        return default
    loc = (request.query_params.get("locale") or request.headers.get("X-Locale") or default).strip().lower()
    return "en" if loc.startswith("en") else "zh"
