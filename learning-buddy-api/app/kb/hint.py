"""Pick the textbook name the File Agent should search for."""

from __future__ import annotations

from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.kb.workspace_store import list_metas


def strip_pdf_name(value: str) -> str:
    text = (value or "").strip()
    if text.lower().endswith(".pdf"):
        return ""
    return text


def clean_textbook_hint(*, hint: str = "", label: str = "", filename: str = "") -> str:
    for candidate in (hint, label, filename):
        cleaned = strip_pdf_name(candidate)
        if cleaned:
            return cleaned
    return ""


def pick_textbook_hint(session: Session | None = None, subject: str = "", grade_band: str = "") -> str:
    rows = _list_books()
    if subject:
        rows = [b for b in rows if str(b.get("subject") or "") == subject] or rows
    if subject and grade_band:
        same_grade = [b for b in rows if str(b.get("grade_band") or "") == grade_band]
        if same_grade:
            rows = same_grade
    ready = [b for b in rows if str(b.get("status") or "") == "ready"]
    if ready:
        rows = ready
    if not rows:
        return ""
    defaults = [b for b in rows if b.get("is_default")]
    chosen = defaults[0] if defaults else rows[0]
    return str(chosen.get("textbook_hint") or chosen.get("label") or "").strip()


def _list_books() -> list[dict[str, Any]]:
    url = (settings.textbook_agent_url or "").strip().rstrip("/")
    if url:
        try:
            headers = {}
            token = (settings.textbook_agent_token or "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = httpx.get(f"{url}/v1/library", headers=headers, timeout=5.0)
            if r.status_code < 400:
                data = r.json()
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict)]
        except Exception:
            pass
    return list_metas()
