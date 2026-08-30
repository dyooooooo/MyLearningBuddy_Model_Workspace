"""Plan-mode files under ``.cursor/plans/``. Shown as a card in the conversation."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from app.config import settings

_HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.M)
_SAFE = re.compile(r"[^a-zA-Z0-9\u4e00-\u9fff_-]+")
_PLAN_FIELD = re.compile(r'"plan"\s*:\s*"')


def plans_dir() -> Path:
    root = settings.cursor_dir / "plans"
    root.mkdir(parents=True, exist_ok=True)
    return root


def snapshot_index() -> dict[str, float]:
    root = plans_dir()
    out: dict[str, float] = {}
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            try:
                out[path.name] = path.stat().st_mtime
            except OSError:
                continue
    return out


def title_from_markdown(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "执行计划"
    hit = _HEADING.search(raw)
    if hit:
        return hit.group(1).strip()[:80]
    return raw.splitlines()[0].strip().lstrip("#").strip()[:80] or "执行计划"


def _slug(title: str, job_id: str) -> str:
    base = _SAFE.sub("-", (title or "").strip())[:40].strip("-")
    stamp = (job_id or "")[:8] or str(int(time.time()))
    return f"{base or 'plan'}-{stamp}"


def _public(path: Path, markdown: str) -> dict[str, Any]:
    title = title_from_markdown(markdown)
    rel = path.relative_to(settings.home).as_posix() if path.is_relative_to(settings.home) else path.name
    return {
        "id": path.stem,
        "title": title,
        "path": rel,
        "markdown": markdown,
    }


def _newest_changed(before: dict[str, float]) -> Path | None:
    after = snapshot_index()
    changed: list[Path] = []
    root = plans_dir()
    for name, mtime in after.items():
        prev = before.get(name)
        if prev is None or mtime > prev + 0.01:
            changed.append(root / name)
    if not changed:
        return None
    return max(changed, key=lambda p: p.stat().st_mtime)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def looks_like_plan(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.lstrip().startswith("#"):
        return True
    if len(raw) < 40:
        return False
    if re.search(r"(?m)^\s*[-*]\s+\S", raw) and "\n" in raw:
        return True
    return bool(re.search(r"(?m)^\s*\d+\.\s+\S", raw))


def _unescape_json_string_prefix(s: str) -> str:
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "u" and i + 5 < n:
                try:
                    out.append(chr(int(s[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
            out.append(mapping.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return "".join(out).strip()


def markdown_from_create_plan_detail(detail: str) -> str:
    text = (detail or "").strip()
    if not text:
        return ""
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            plan = obj.get("plan") or obj.get("markdown") or obj.get("content")
            if isinstance(plan, str) and plan.strip():
                return plan.strip()
        if isinstance(obj, str) and looks_like_plan(obj):
            return obj.strip()
    except (json.JSONDecodeError, TypeError):
        pass
    hit = _PLAN_FIELD.search(text)
    if hit:
        return _unescape_json_string_prefix(text[hit.end() :])
    if text.lstrip().startswith("#") and len(text) > 40:
        return text.strip()
    return ""


def markdown_from_trace(trace: list[Any] | None) -> str:
    if not isinstance(trace, list):
        return ""
    for step in reversed(trace):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "")
        if "createplan" not in title.lower():
            continue
        md = markdown_from_create_plan_detail(str(step.get("detail") or ""))
        if md:
            return md
    return ""


def plan_markdown(reply: str, before: dict[str, float] | None = None, trace: list[Any] | None = None) -> str:
    found = _newest_changed(before or {})
    if found:
        text = _read(found).strip()
        if looks_like_plan(text):
            return text
    md = markdown_from_trace(trace)
    if looks_like_plan(md):
        return md
    body = (reply or "").strip()
    return body if looks_like_plan(body) else ""


def capture_after_plan(reply: str, before: dict[str, float] | None = None, job_id: str = "") -> dict[str, Any]:
    """Use a newly written plan file, or persist the model reply ourselves."""
    found = _newest_changed(before or {})
    body = (reply or "").strip()
    if found:
        text = _read(found).strip() or body
        if text:
            return _public(found, text)
    if not body:
        return {}
    title = title_from_markdown(body)
    dest = plans_dir() / f"{_slug(title, job_id)}.md"
    if not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body}"
    dest.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    return _public(dest, body)


def finalize(
    reply: str,
    before: dict[str, float] | None = None,
    job_id: str = "",
    conversation_id: str = "",
    trace: list[Any] | None = None,
) -> dict[str, Any]:
    """Persist the plan file and take a post-plan checkpoint for card rollback."""
    md = plan_markdown(reply, before, trace)
    row = capture_after_plan(md, before, job_id)
    if not row:
        return {}
    from app import checkpoints

    snap = checkpoints.snapshot(
        "plan",
        str(row.get("title") or "plan"),
        job_id=job_id,
        conversation_id=conversation_id,
        force=True,
    )
    row["checkpoint"] = str(snap.get("id") or "")
    return row


def hydrate(job: dict[str, Any]) -> bool:
    """Backfill ``result.plan`` for an old Plan job that only has ``createPlan``."""
    if not isinstance(job, dict):
        return False
    if str(job.get("status") or "") != "completed":
        return False
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    mode = str((payload or {}).get("mode") or (result or {}).get("mode") or "").lower()
    if mode != "plan":
        return False
    existing = (result or {}).get("plan")
    if isinstance(existing, dict) and str(existing.get("markdown") or "").strip():
        return False
    md = plan_markdown(str((result or {}).get("reply") or ""), {}, job.get("trace") or [])
    if not md:
        return False
    row = capture_after_plan(md, {}, str(job.get("job_id") or ""))
    if not row:
        return False
    row["checkpoint"] = str((result or {}).get("checkpoint") or "")
    from app import jobs

    jobs.patch_result(str(job.get("job_id") or ""), persist=True, plan=row)
    merged = dict(result or {})
    merged["plan"] = row
    job["result"] = merged
    return True
