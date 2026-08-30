"""Code-edit approval tickets. Only this process writes memory/approvals/."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import settings

FOUR_FIELDS = ("situation", "why", "changes", "impact")
MIN_FIELD = 20
TICKET_TTL_S = 15 * 60
_ID_OK = re.compile(r"^[0-9a-f]{8,32}$")
_KNOWN_HOME = "/data/agent"


def norm_path(raw: str, home: str | Path | None = None) -> str:
    """Workspace-relative POSIX path. Bare ``foo.py`` becomes ``tools/foo.py``."""
    compact = (raw or "").replace("\\", "/").strip()
    if not compact:
        return ""
    home_s = str(home or settings.home).replace("\\", "/").rstrip("/")
    if compact.startswith(home_s + "/"):
        compact = compact[len(home_s) + 1 :]
    elif compact == home_s:
        return ""
    known = _KNOWN_HOME.rstrip("/") + "/"
    if compact.startswith(known):
        compact = compact[len(known) :]
    while compact.startswith("./"):
        compact = compact[2:]
    compact = compact.lstrip("/")
    if "/" not in compact and compact.endswith(".py"):
        compact = f"tools/{compact}"
    return compact


def infer_scope(path: str, home: str | Path | None = None) -> str:
    rel = norm_path(path, home=home)
    if rel.startswith("tools/") and rel.endswith(".py"):
        return "tools"
    if rel.startswith(".cursor/hooks/"):
        return "hooks"
    if rel.startswith("surfaces/"):
        return "surfaces"
    return ""


def is_managed_path(path: str, home: str | Path | None = None) -> bool:
    return bool(infer_scope(path, home=home))


def validate(fields: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for name in FOUR_FIELDS:
        text = str(fields.get(name) or "").strip()
        if len(text) < MIN_FIELD:
            missing.append(name)
    return missing


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: datetime) -> str:
    return when.isoformat()


def _root() -> Path:
    root = settings.memory_dir / "approvals"
    for name in ("pending", "granted", "refused", "revised"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _req_path(kind: str, req_id: str) -> Path:
    req_id = (req_id or "").strip().lower()
    if not _ID_OK.fullmatch(req_id):
        raise ValueError("invalid approval id")
    return _root() / kind / f"{req_id}.json"


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("approval record is not an object")
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def open_request(
    path: str,
    situation: str,
    why: str,
    changes: str,
    impact: str,
    *,
    tool_name: str = "request_code_edit",
    scope: str = "",
    job_id: str = "",
    conversation_id: str = "",
) -> dict[str, Any]:
    rel = norm_path(path)
    got_scope = (scope or "").strip() or infer_scope(rel)
    if not got_scope:
        raise ValueError("path is not a managed code path")
    fields = {
        "situation": (situation or "").strip(),
        "why": (why or "").strip(),
        "changes": (changes or "").strip(),
        "impact": (impact or "").strip(),
    }
    missing = validate(fields)
    if missing:
        raise ValueError("incomplete brief: " + ",".join(missing))
    req_id = uuid.uuid4().hex
    record = {
        "req_id": req_id,
        "status": "pending",
        "path": rel,
        "scope": got_scope,
        "tool_name": (tool_name or "request_code_edit").strip(),
        "job_id": (job_id or "").strip(),
        "conversation_id": (conversation_id or "").strip(),
        "requested_at": _iso(_now()),
        **fields,
    }
    _write(_req_path("pending", req_id), record)
    return record


def load_pending(req_id: str) -> dict[str, Any] | None:
    path = _req_path("pending", req_id)
    if not path.is_file():
        return None
    try:
        return _read(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def list_pending() -> list[dict[str, Any]]:
    root = _root() / "pending"
    rows: list[dict[str, Any]] = []
    for item in sorted(root.glob("*.json")):
        try:
            data = _read(item)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        rows.append(data)
    return rows


def _take_pending(req_id: str) -> dict[str, Any]:
    path = _req_path("pending", req_id)
    if not path.is_file():
        raise FileNotFoundError(req_id)
    data = _read(path)
    path.unlink()
    return data


def grant(req_id: str, job_id: str = "", conversation_id: str = "") -> dict[str, Any]:
    data = _take_pending(req_id)
    now = _now()
    data.update(
        {
            "status": "granted",
            "job_id": (job_id or "").strip() or str(data.get("job_id") or ""),
            "conversation_id": (conversation_id or "").strip() or str(data.get("conversation_id") or ""),
            "granted_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=TICKET_TTL_S)),
            "one_shot": True,
        }
    )
    _write(_req_path("granted", req_id), data)
    return data


def refuse(req_id: str, note: str = "") -> dict[str, Any]:
    data = _take_pending(req_id)
    data.update(
        {
            "status": "refused",
            "note": (note or "").strip(),
            "refused_at": _iso(_now()),
        }
    )
    _write(_req_path("refused", req_id), data)
    return data


def revise(req_id: str, note: str) -> dict[str, Any]:
    text = (note or "").strip()
    if not text:
        raise ValueError("revise 需要填写想法")
    data = _take_pending(req_id)
    data.update(
        {
            "status": "revised",
            "note": text,
            "revised_at": _iso(_now()),
        }
    )
    _write(_req_path("revised", req_id), data)
    return data
