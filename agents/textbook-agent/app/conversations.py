"""One Cursor-style thread: many chat jobs, one Agent-written title."""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_convs: dict[str, dict[str, Any]] = {}
_loaded_home = ""
_PREFIX = re.compile(r"^(?:请你|请帮我|帮我|麻烦你|麻烦|能不能|可以帮我|可以|请问)\s*")
_JUNK_TITLE = re.compile(r"^[#>*\-\s\"'「」『』]+|[#\"'「」『』]+$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def reset_for_tests() -> None:
    global _loaded_home
    with _lock:
        _convs.clear()
        _loaded_home = ""


def fallback_title(message: str) -> str:
    """Placeholder until the Agent writes a real title."""
    raw = (message or "").strip()
    line = raw.splitlines()[0].strip() if raw else ""
    line = _PREFIX.sub("", line).strip() or line or raw
    line = re.sub(r"\s+", " ", line)
    if not line:
        return "新对话"
    if len(line) > 28:
        return f"{line[:27]}…"
    return line


def clean_agent_title(text: str) -> str:
    line = ""
    for raw in (text or "").splitlines():
        bit = _JUNK_TITLE.sub("", raw.strip()).strip()
        if bit:
            line = bit
            break
    line = re.sub(r"\s+", " ", line)
    line = line.strip("。．.！!？?；;：:")
    if not line or len(line) > 40:
        return ""
    if len(line) > 18:
        line = line[:18]
    return line


def make_title(message: str) -> str:
    return fallback_title(message)


def _dir() -> Path:
    path = settings.traces_dir / "conversations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(cid: str) -> Path:
    return _dir() / f"{cid}.json"


def _load() -> None:
    global _loaded_home
    home = str(settings.home)
    if _loaded_home == home:
        return
    _convs.clear()
    _loaded_home = home
    root = settings.traces_dir / "conversations"
    if not root.is_dir():
        return
    for path in root.glob("*.json"):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict) and item.get("id"):
            _convs[str(item["id"])] = item


def _persist(row: dict[str, Any]) -> None:
    cid = str(row.get("id") or "")
    if not cid:
        return
    try:
        _path(cid).write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _drop(cid: str) -> None:
    _convs.pop(cid, None)
    try:
        _path(cid).unlink(missing_ok=True)
    except OSError:
        pass


def _aliases(row: dict[str, Any]) -> set[str]:
    out = {str(row.get("id") or ""), str(row.get("sdk_id") or "")}
    for key in ("alias_ids", "sdk_ids"):
        for item in row.get(key) or []:
            out.add(str(item or ""))
    out.discard("")
    out.discard("none")
    out.discard("local")
    return out


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "title": row.get("title") or "新对话",
        "title_source": row.get("title_source") or "fallback",
        "sdk_id": row.get("sdk_id") or "",
        "created_at": row.get("created_at") or "",
        "updated_at": row.get("updated_at") or "",
        "job_ids": list(row.get("job_ids") or []),
    }


def get(cid: str) -> dict[str, Any] | None:
    _load()
    key = (cid or "").strip()
    if not key:
        return None
    with _lock:
        row = _convs.get(key)
        if row:
            return dict(row)
        for item in _convs.values():
            if key in _aliases(item):
                return dict(item)
    return None


def ensure(conversation_id: str = "", message: str = "") -> dict[str, Any]:
    _load()
    coalesce_linked_threads()
    cid = (conversation_id or "").strip()
    if cid in ("none", "local"):
        cid = ""
    found = get(cid) if cid else None
    if found:
        with _lock:
            row = _convs.get(str(found["id"]))
            if row is None:
                return found
            if not row.get("title") and message.strip():
                row["title"] = fallback_title(message)
                row["title_source"] = "fallback"
            row["updated_at"] = _now()
            _persist(row)
            return dict(row)
    with _lock:
        created = not cid
        cid = cid or str(uuid.uuid4())
        row = {
            "id": cid,
            "title": fallback_title(message),
            "title_source": "fallback",
            "sdk_id": "" if created else cid,
            "alias_ids": [] if created else [cid],
            "created_at": _now(),
            "updated_at": _now(),
            "job_ids": [],
        }
        _convs[cid] = row
        _persist(row)
        return dict(row)


def attach_job(conversation_id: str, job_id: str) -> None:
    row = get(conversation_id)
    jid = (job_id or "").strip()
    if row is None or not jid:
        return
    with _lock:
        live = _convs.get(str(row["id"]))
        if live is None:
            return
        ids = list(live.get("job_ids") or [])
        if jid not in ids:
            ids.append(jid)
            live["job_ids"] = ids
        live["updated_at"] = _now()
        _persist(live)


def bind_sdk(conversation_id: str, sdk_id: str) -> None:
    row = get(conversation_id)
    sid = (sdk_id or "").strip()
    if row is None or not sid or sid in ("none", "local"):
        return
    with _lock:
        live = _convs.get(str(row["id"]))
        if live is None:
            return
        live["sdk_id"] = sid
        aliases = list(live.get("alias_ids") or [])
        if sid not in aliases and sid != live.get("id"):
            aliases.append(sid)
            live["alias_ids"] = aliases
        live["updated_at"] = _now()
        _persist(live)


def resume_id(conversation_id: str) -> str:
    row = get(conversation_id)
    if not row:
        return (conversation_id or "").strip()
    return str(row.get("sdk_id") or "")


def set_title(conversation_id: str, title: str, source: str = "agent") -> str:
    row = get(conversation_id)
    cleaned = clean_agent_title(title) if source == "agent" else (title or "").strip()
    if row is None or not cleaned:
        return ""
    with _lock:
        live = _convs.get(str(row["id"]))
        if live is None:
            return ""
        live["title"] = cleaned
        live["title_source"] = source
        live["updated_at"] = _now()
        _persist(live)
        job_ids = list(live.get("job_ids") or [])
        cid = str(live["id"])
    from app import jobs

    if job_ids:
        jobs.patch_result(str(job_ids[-1]), title=cleaned, conversation_id=cid)
    return cleaned


def schedule_title(
    conversation_id: str,
    summarizer: Callable[[str], str],
    current_user: str = "",
    current_reply: str = "",
) -> None:
    row = get(conversation_id)
    if row is None or row.get("title_source") in ("agent", "pending"):
        return
    with _lock:
        live = _convs.get(str(row["id"]))
        if live is None or live.get("title_source") in ("agent", "pending"):
            return
        live["title_source"] = "pending"
        _persist(live)

    def _run() -> None:
        try:
            blob = title_blob(conversation_id, current_user, current_reply)
            if not blob.strip():
                return
            raw = summarizer(blob) or ""
            if set_title(conversation_id, raw, "agent"):
                return
        except Exception:
            logger.warning("conversation title agent failed", exc_info=True)

    threading.Thread(target=_run, name="conv-title", daemon=True).start()


def title_blob(conversation_id: str, extra_user: str = "", extra_reply: str = "") -> str:
    lines: list[str] = []
    seen: set[str] = set()
    extra = (extra_user or "").strip()
    if extra:
        lines.append(f"用户：{extra[:240]}")
        seen.add(extra)
        if extra_reply.strip():
            lines.append(f"助手：{extra_reply.strip()[:180]}")
    for job in conversation_jobs(conversation_id)[:8]:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        msg = str(payload.get("message") or "").strip()
        if msg and msg not in seen:
            lines.append(f"用户：{msg[:240]}")
            seen.add(msg)
        reply = str(result.get("reply") or "").strip()
        if reply:
            lines.append(f"助手：{reply[:180]}")
    return "\n".join(lines)


def _job_cids(job: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    for src in (payload, result):
        cid = str(src.get("conversation_id") or "").strip()
        if cid and cid not in ("none", "local"):
            out.add(cid)
    return out


def coalesce_linked_threads() -> None:
    """Merge jobs that hand the Cursor agent id from one turn to the next."""
    from app import jobs

    _load()
    chat = [j for j in jobs.list_jobs(200) if str(j.get("kind") or "") == "chat"]
    if not chat:
        return
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for job in chat:
        cids = list(_job_cids(job))
        if not cids:
            cids = [f"job:{job.get('job_id')}"]
        for other in cids[1:]:
            union(cids[0], other)

    groups: dict[str, list[dict[str, Any]]] = {}
    for job in chat:
        cids = _job_cids(job) or {f"job:{job.get('job_id')}"}
        groups.setdefault(find(next(iter(cids))), []).append(job)

    with _lock:
        for members in groups.values():
            _merge_group_locked(members)


def _merge_group_locked(members: list[dict[str, Any]]) -> None:
    cids: set[str] = set()
    job_ids: list[str] = []
    for job in sorted(members, key=lambda j: str(j.get("created_at") or "")):
        cids |= _job_cids(job)
        jid = str(job.get("job_id") or "")
        if jid and jid not in job_ids:
            job_ids.append(jid)
    if not job_ids:
        return
    matches = [row for row in _convs.values() if _aliases(row) & cids or set(row.get("job_ids") or []) & set(job_ids)]
    matches.sort(key=lambda r: str(r.get("created_at") or ""))
    if matches:
        keep = matches[0]
        extras = matches[1:]
    else:
        oldest = min(members, key=lambda j: str(j.get("created_at") or ""))
        cid = next(iter(_job_cids(oldest)), str(uuid.uuid4()))
        keep = {
            "id": cid,
            "title": fallback_title(str((oldest.get("payload") or {}).get("message") or "")),
            "title_source": "fallback",
            "sdk_id": "",
            "alias_ids": [],
            "created_at": oldest.get("created_at") or _now(),
            "updated_at": oldest.get("updated_at") or _now(),
            "job_ids": [],
        }
        _convs[cid] = keep
        extras = []
    aliases = _aliases(keep) | cids
    aliases.discard(str(keep.get("id") or ""))
    latest = max(members, key=lambda j: str(j.get("updated_at") or ""))
    latest_sdk = ""
    result = latest.get("result") if isinstance(latest.get("result"), dict) else {}
    latest_sdk = str(result.get("conversation_id") or "").strip()
    if latest_sdk in ("none", "local"):
        latest_sdk = ""
    keep["job_ids"] = job_ids
    keep["alias_ids"] = sorted(aliases)
    if latest_sdk:
        keep["sdk_id"] = latest_sdk
    elif not keep.get("sdk_id") and aliases:
        keep["sdk_id"] = next(iter(aliases))
    keep["updated_at"] = max(str(j.get("updated_at") or "") for j in members) or _now()
    first_msg = ""
    for job in members:
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        first_msg = str(payload.get("message") or "")
        if first_msg:
            break
    if keep.get("title_source") != "agent":
        keep["title"] = keep.get("title") or fallback_title(first_msg)
        keep.setdefault("title_source", "fallback")
    _persist(keep)
    for extra in extras:
        eid = str(extra.get("id") or "")
        if eid and eid != keep.get("id"):
            _drop(eid)


def _backfill_from_jobs() -> None:
    from app import jobs

    for job in jobs.list_jobs(200):
        if str(job.get("kind") or "") != "chat":
            continue
        cids = _job_cids(job)
        if not cids:
            continue
        found = None
        with _lock:
            for cid in cids:
                for row in _convs.values():
                    if cid in _aliases(row):
                        found = row
                        break
                if found:
                    break
        if found:
            attach_job(str(found["id"]), str(job.get("job_id") or ""))
            continue
        cid = next(iter(cids))
        message = ""
        payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        message = str(payload.get("message") or "")
        with _lock:
            if cid not in _convs:
                _convs[cid] = {
                    "id": cid,
                    "title": fallback_title(message),
                    "title_source": "fallback",
                    "sdk_id": cid,
                    "alias_ids": [cid],
                    "created_at": job.get("created_at") or _now(),
                    "updated_at": job.get("updated_at") or _now(),
                    "job_ids": [],
                }
                _persist(_convs[cid])
        attach_job(cid, str(job.get("job_id") or ""))


def list_conversations(limit: int = 80) -> list[dict[str, Any]]:
    _load()
    coalesce_linked_threads()
    _backfill_from_jobs()
    from app import jobs

    job_map = {str(j.get("job_id")): j for j in jobs.list_jobs(200)}
    with _lock:
        rows = list(_convs.values())
    out: list[dict[str, Any]] = []
    for row in rows:
        ids = [str(j) for j in (row.get("job_ids") or [])]
        thread = [job_map[j] for j in ids if j in job_map]
        statuses = [str(j.get("status") or "") for j in thread]
        if "running" in statuses:
            status = "running"
        elif "queued" in statuses:
            status = "queued"
        elif "failed" in statuses and not any(s == "completed" for s in statuses):
            status = "failed"
        elif thread:
            status = str(thread[-1].get("status") or "completed")
        else:
            status = "completed"
        preview = ""
        if thread:
            payload = thread[0].get("payload") if isinstance(thread[0].get("payload"), dict) else {}
            preview = str(payload.get("message") or "").replace("\n", " ").strip()[:42]
        item = public_view(row)
        item["status"] = status
        item["turns"] = len(ids)
        item["preview"] = preview
        out.append(item)
    out.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return out[: max(1, min(limit, 200))]


def conversation_jobs(conversation_id: str) -> list[dict[str, Any]]:
    row = get(conversation_id)
    if row is None:
        return []
    from app import jobs

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for jid in row.get("job_ids") or []:
        job = jobs.get_job(str(jid))
        if job:
            out.append(jobs.public_view(job))
            seen.add(str(jid))
    aliases = _aliases(row)
    if aliases:
        for job in jobs.list_jobs(200):
            jid = str(job.get("job_id") or "")
            if jid in seen or str(job.get("kind") or "") != "chat":
                continue
            if _job_cids(job) & aliases:
                out.append(job)
                seen.add(jid)
    out.sort(key=lambda j: str(j.get("created_at") or ""))
    return out
