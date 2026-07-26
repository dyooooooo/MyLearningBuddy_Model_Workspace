"""HTTP client to learning-buddy-api (A / B / A+ authoritative store)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.schemas import HomeworkEntry, PagePart

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def _headers(student_id: str, *, locale: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {settings.buddy_api_token}",
        "X-Student-Id": student_id,
    }
    if locale:
        h["X-Locale"] = locale
    return h


def _locale_from_request(request) -> str | None:
    if request is None:
        return None
    loc = (request.headers.get("X-Locale") or "").strip()
    return loc or None


def _client_get() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=settings.buddy_api_url.rstrip("/"),
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _part_payload(p: PagePart) -> dict[str, Any]:
    return {
        "kind": p.kind,
        "id": p.id,
        "print_text": p.print_text,
        "handwriting": p.handwriting,
        "bbox_print": p.bbox_print,
        "bbox_handwriting": p.bbox_handwriting,
    }


async def ingest_homework(student_id: str, entry: HomeworkEntry) -> dict | None:
    meta = dict(entry.meta or {})
    meta.setdefault("subject", meta.get("subject") or "math")
    meta.setdefault("grade_band", meta.get("grade_band") or "hs")
    body = {
        "id": entry.id,
        "session_id": entry.session_id,
        "page_id": entry.page_id,
        "finalized_at": entry.finalized_at,
        "source_frame_ids": entry.source_frame_ids,
        "parts": [_part_payload(p) for p in entry.parts],
        "meta": meta,
    }
    try:
        r = await _client_get().post(
            f"/api/v1/students/{student_id}/ingest/homework",
            json=body,
            headers=_headers(student_id),
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("Buddy ingest_homework failed: %s", e)
        return None


async def ingest_memory_events(
    student_id: str,
    session_id: str,
    events: list[dict[str, Any]],
) -> bool:
    if not events:
        return True
    try:
        r = await _client_get().post(
            f"/api/v1/students/{student_id}/ingest/events",
            json={"events": events},
            headers=_headers(student_id),
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("Buddy ingest_events failed: %s", e)
        return False


async def emit_handwriting_growth(
    student_id: str,
    session_id: str,
    question_id: str,
    growth: int,
) -> None:
    await ingest_memory_events(
        student_id,
        session_id,
        [
            {
                "session_id": session_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": "HANDWRITING_GROWTH",
                "question_id": question_id,
                "payload": {"growth_chars": growth},
            }
        ],
    )


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {settings.buddy_admin_token}"}


async def list_students_admin() -> list[dict[str, Any]]:
    client = _client_get()
    list_r = await client.get("/api/v1/admin/students", headers=_admin_headers())
    list_r.raise_for_status()
    return list_r.json()


async def ensure_student(
    student_id: str,
    *,
    label: str = "",
    grade_band: str = "hs",
) -> dict[str, Any]:
    """Register student via Admin API if missing; never expose admin token to clients."""
    client = _client_get()
    list_r = await client.get("/api/v1/admin/students", headers=_admin_headers())
    list_r.raise_for_status()
    for row in list_r.json():
        if row.get("id") == student_id:
            tier_r = await client.get(
                f"/api/v1/students/{student_id}/tier",
                headers=_headers(student_id),
            )
            tier = tier_r.json() if tier_r.status_code == 200 else {}
            return {
                "id": student_id,
                "created": False,
                "label": row.get("label", student_id),
                "grade_band": row.get("grade_band", grade_band),
                "tier": tier.get("tier", "trial"),
            }

    create_r = await client.post(
        "/api/v1/admin/students",
        json={"id": student_id, "label": label or student_id, "grade_band": grade_band},
        headers=_admin_headers(),
    )
    create_r.raise_for_status()
    created = create_r.json()
    return {
        "id": student_id,
        "created": True,
        "label": created.get("label", student_id),
        "grade_band": created.get("grade_band", grade_band),
        "tier": "trial",
    }


async def proxy_get(path: str, student_id: str, request=None) -> httpx.Response:
    return await _client_get().get(path, headers=_headers(student_id, locale=_locale_from_request(request)))


async def proxy_patch(path: str, body: dict, student_id: str, request=None) -> httpx.Response:
    return await _client_get().patch(path, json=body, headers=_headers(student_id, locale=_locale_from_request(request)))


async def proxy_post(path: str, body: dict, student_id: str, request=None) -> httpx.Response:
    return await _client_get().post(path, json=body, headers=_headers(student_id, locale=_locale_from_request(request)))


async def proxy_delete(path: str, student_id: str, request=None) -> httpx.Response:
    return await _client_get().delete(path, headers=_headers(student_id, locale=_locale_from_request(request)))


async def normalize_page_parts(parts: list[PagePart]) -> list[PagePart]:
    if not parts:
        return parts
    body = {
        "parts": [{"print": p.print_text, "handwriting": p.handwriting} for p in parts],
    }
    r = await _client_get().post(
        "/api/v1/math/normalize-parts",
        json=body,
        headers={"Authorization": f"Bearer {settings.buddy_api_token}"},
    )
    r.raise_for_status()
    rows = r.json().get("parts", [])
    out: list[PagePart] = []
    for part, row in zip(parts, rows):
        copy = part.model_copy(deep=True)
        copy.print_rich = row.get("print_rich")
        copy.handwriting_rich = row.get("handwriting_rich")
        out.append(copy)
    return out


async def rag_search(
    q: str,
    *,
    student_id: str,
    subject: str = "math",
    grade_band: str = "hs",
    top_k: int = 3,
) -> list[dict[str, Any]]:
    try:
        r = await _client_get().get(
            "/api/v1/rag/search",
            params={"q": q, "subject": subject, "grade_band": grade_band, "top_k": top_k},
            headers=_headers(student_id),
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("Buddy rag_search failed: %s", e)
        return []


async def corpus_admin_request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    content: bytes | None = None,
    extra_headers: dict | None = None,
) -> httpx.Response:
    client = _client_get()
    headers = _admin_headers()
    if extra_headers:
        headers = {**headers, **extra_headers}
    return await client.request(
        method,
        path,
        json=json if content is None else None,
        content=content,
        params=params,
        headers=headers,
    )
