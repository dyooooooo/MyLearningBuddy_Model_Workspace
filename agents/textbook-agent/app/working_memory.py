"""Disk working notes: task pointer per conversation, decisions file read-only."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.resume_context import _step_line, job_message, original_task

logger = logging.getLogger(__name__)

SAFE_CID = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
WORKING_MAX = 2000
DECISIONS_MAX = 1500


def _focus_line(job: dict[str, Any] | None, conversation_id: str) -> str:
    payload = job.get("payload") if job and isinstance(job.get("payload"), dict) else {}
    focus: dict[str, Any] = {}
    for key in ("book_id", "book", "page_no", "block_id", "block_label"):
        value = (payload or {}).get(key)
        if value not in (None, "", 0):
            focus[key] = value
    if not focus and conversation_id:
        from app import conversations

        row = conversations.get(conversation_id)
        stored = (row or {}).get("focus") if row else None
        if isinstance(stored, dict):
            focus = {k: v for k, v in stored.items() if v not in (None, "", 0)}
    if not focus:
        return ""
    book = str(focus.get("book") or focus.get("book_id") or "").strip()
    try:
        page = int(focus.get("page_no") or 0)
    except (TypeError, ValueError):
        page = 0
    block = str(focus.get("block_id") or "").strip()
    label = str(focus.get("block_label") or "").strip()
    bits = ["聚焦："]
    if book:
        bits.append(f"书 {book}")
    if page > 0:
        bits.append(f"第 {page} 页")
    if block:
        bits.append(f"块 {block}")
    if label:
        bits.append(f"「{label}」")
    if len(bits) <= 1:
        return ""
    return " ".join(bits)


def conversation_id_of(job: dict[str, Any] | None) -> str:
    if not job:
        return ""
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    cid = str((payload or {}).get("conversation_id") or "").strip()
    if cid and cid not in ("none", "local"):
        return cid
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    cid = str((result or {}).get("conversation_id") or "").strip()
    if cid in ("none", "local"):
        return ""
    return cid


def working_path(conversation_id: str) -> Path | None:
    cid = (conversation_id or "").strip()
    if not SAFE_CID.match(cid):
        return None
    root = settings.memory_dir / "working"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{cid}.md"


def decisions_path() -> Path:
    settings.memory_dir.mkdir(parents=True, exist_ok=True)
    return settings.memory_dir / "DECISIONS.md"


def write_from_job(job: dict[str, Any] | None) -> str:
    """Overwrite the conversation working note. Returns the path or empty."""
    if not job or job.get("kind") != "chat":
        return ""
    cid = conversation_id_of(job)
    dest = working_path(cid)
    if dest is None:
        return ""
    from app import conversations

    thread = [row for row in conversations.conversation_jobs(cid) if row]
    if not any(str(row.get("job_id") or "") == str(job.get("job_id") or "") for row in thread):
        thread = list(thread) + [job]
    task = original_task(thread) if thread else job_message(job)
    last_user = job_message(job)
    status = str(job.get("status") or "")
    error = str(job.get("error") or "").strip()
    if status == "failed" and error:
        why = error
    elif status == "cancelled":
        why = error or "上一轮被停住"
    elif status == "completed":
        why = "上一轮已结束"
    else:
        why = "上一轮没有做完"
    steps: list[str] = []
    for step in reversed(list(job.get("trace") or [])):
        line = _step_line(step) if isinstance(step, dict) else ""
        if not line:
            continue
        steps.append(line)
        if len(steps) >= 8:
            break
    steps.reverse()
    tail = ""
    for step in reversed(list(job.get("trace") or [])):
        if not isinstance(step, dict):
            continue
        if str(step.get("kind") or "") in ("think", "answer") and str(step.get("detail") or "").strip():
            tail = str(step.get("detail") or "").strip()[:400]
            break
    lines = [
        "# 工作笔记",
        f"对话：{cid}",
        f"状态：{status or 'unknown'}",
        f"用户任务：{task or last_user or '（无）'}",
    ]
    focus_line = _focus_line(job, cid)
    if focus_line:
        lines.append(focus_line)
    if last_user and last_user != task:
        lines.append(f"本轮原话：{last_user}")
    lines.append(f"停住原因：{why}")
    if steps:
        lines.extend(["", "已经做过：", *steps])
    if tail:
        lines.extend(["", "最后思考/作答：", tail])
    text = "\n".join(lines).strip() + "\n"
    if len(text) > WORKING_MAX:
        text = text[: WORKING_MAX - 1].rstrip() + "…\n"
    dest.write_text(text, encoding="utf-8")
    return str(dest)


def _read_capped(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) > limit:
        return text[-limit:].lstrip()
    return text


def prompt_block(conversation_id: str) -> str:
    """Working note + decisions tail for prompt injection. Empty if nothing on disk."""
    parts: list[str] = []
    dest = working_path((conversation_id or "").strip())
    if dest is not None:
        note = _read_capped(dest, WORKING_MAX)
        if note:
            parts.append("【工作笔记】\n" + note)
    decisions = _read_capped(decisions_path(), DECISIONS_MAX)
    if decisions:
        parts.append("【跨对话结论】\n" + decisions)
    if not parts:
        return ""
    return "\n\n".join(parts).strip() + "\n"
