"""Breakpoint resume: Cursor-style follow-up, not a new hunt through old chats.

Cursor keeps one Agent and ``send()``s the next line. After a bridge timeout we
often lose that handle, so the next 「继续」 used to create a blank agent. This
module pins the *immediately previous turn* and forbids transcript archaeology.
"""

from __future__ import annotations

import re
from typing import Any

PURE_CONTINUE = re.compile(
    r"^(请)?"
    r"(继续|接着|往下|再试(一次|一下)?|continue|resume|keep going|go on)"
    r"(做|完成|吧)?"
    r"[。.!！…\s]*$",
    re.IGNORECASE,
)
NUDGE = re.compile(r"(继续|接着做|再试一次|再试一下|请再试)")
SKIP_TRACE = {
    "connect",
    "still running",
    "bridge retry",
    "status",
    "status running",
    "request",
}


def is_continue(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if PURE_CONTINUE.match(text):
        return True
    return bool(NUDGE.search(text) and len(text) <= 40)


def job_message(job: dict[str, Any] | None) -> str:
    if not job:
        return ""
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    return str((payload or {}).get("message") or (payload or {}).get("problem_text") or "").strip()


def previous_job(conversation_id: str, exclude_job_id: str = "") -> dict[str, Any] | None:
    from app import conversations

    cid = (conversation_id or "").strip()
    if not cid:
        return None
    skip = (exclude_job_id or "").strip()
    rows = [row for row in conversations.conversation_jobs(cid) if str(row.get("job_id") or "") != skip]
    return rows[-1] if rows else None


def original_task(jobs: list[dict[str, Any]], start: int = -1) -> str:
    if not jobs:
        return ""
    idx = len(jobs) + start if start < 0 else start
    idx = min(max(idx, 0), len(jobs) - 1)
    for row in reversed(jobs[: idx + 1]):
        text = job_message(row)
        if text and not is_continue(text):
            return text
    return job_message(jobs[idx])


def _step_line(step: dict[str, Any]) -> str:
    title = str(step.get("title") or "").strip()
    if not title or title.lower() in SKIP_TRACE:
        return ""
    if title.lower().startswith("still running") or title.lower().startswith("status"):
        return ""
    detail = str(step.get("detail") or "").replace("\n", " ").strip()
    if len(detail) > 160:
        detail = detail[:157] + "…"
    if detail:
        return f"- {title}：{detail}"
    return f"- {title}"


def format_brief(job: dict[str, Any] | None, *, user_now: str = "", task: str = "") -> str:
    if not job:
        return ""
    status = str(job.get("status") or "")
    error = str(job.get("error") or "").strip()
    if status == "failed" and error:
        why = error
    elif status == "cancelled":
        why = "上一轮被停住"
    elif status == "completed":
        why = "上一轮已结束，按同一任务往下做"
    else:
        why = "上一轮没有做完"
    last_user = job_message(job)
    task = (task or "").strip() or original_task([job], 0)
    if last_user and last_user != task:
        task_block = f"{task}\n（上一轮原话：{last_user}）"
    else:
        task_block = task or last_user or "（无）"
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
        kind = str(step.get("kind") or "")
        if kind in ("think", "answer") and str(step.get("detail") or "").strip():
            tail = str(step.get("detail") or "").strip()[:400]
            break
    lines = [
        "【断点续传】上一轮停在下面这个位置。从这里接着做完。",
        "不要重新调查，不要打开 traces/、.cursor/projects/*/agent-transcripts、前几轮对话。",
        "",
        f"停住原因：{why}",
        "",
        "上一轮用户任务：",
        task_block,
    ]
    if steps:
        lines.extend(["", "上一轮已经做过：", *steps])
    if tail:
        lines.extend(["", "最后思考/作答：", tail])
    now = (user_now or "").strip()
    if now and now != last_user:
        lines.extend(["", "用户现在说：", now])
    return "\n".join(lines).strip() + "\n"


def compose_prompts(
    message: str,
    *,
    system: str,
    conversation_id: str = "",
    job_id: str = "",
) -> tuple[str, str, dict[str, Any]]:
    """Return (full_prompt, followup_prompt, meta).

    ``full_prompt`` is for a newly created Agent (no Cursor history).
    ``followup_prompt`` is what Cursor ``send()`` after ``Agent.resume``.
    """
    from app import conversations, working_memory

    text = message or ""
    extra = ""
    cid = (conversation_id or "").strip()
    notes = working_memory.prompt_block(cid) if cid else ""
    if cid:
        thread = [
            row
            for row in conversations.conversation_jobs(cid)
            if str(row.get("job_id") or "") != (job_id or "").strip()
        ]
        prev = thread[-1] if thread else None
        if prev is not None and is_continue(text):
            extra = format_brief(prev, user_now=text, task=original_task(thread))
    if extra and is_continue(text):
        body = extra.strip() + "\n"
    elif extra:
        body = (extra + "\n" + text).strip()
    else:
        body = text
    chunks = [bit.strip() for bit in (notes, body) if bit and bit.strip()]
    followup = "\n\n".join(chunks)
    if followup and not followup.endswith("\n"):
        followup += "\n"
    full = (system or "") + followup
    return full, followup, {"has_breakpoint": bool(extra), "resuming": is_continue(text)}
