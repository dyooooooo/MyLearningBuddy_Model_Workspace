from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def buddy_post(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    from app.config import settings

    base = (settings.buddy_api_url or "").rstrip("/")
    if not base:
        raise RuntimeError("buddy_api_url is empty")
    url = f"{base}{path}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    token = (settings.buddy_admin_token or "").strip()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            code = int(resp.status)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"buddy-api {exc.code} {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"buddy-api unreachable {path}: {exc}") from exc
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        parsed = {"raw": raw[:400]}
    if not isinstance(parsed, dict):
        parsed = {"result": parsed}
    parsed.setdefault("http_status", code)
    return parsed
