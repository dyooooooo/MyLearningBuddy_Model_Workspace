"""After a connect stall, enqueue one breakpoint continue. Then stop and let the UI ask."""

from __future__ import annotations

import logging
from typing import Any

from app import checkpoints, conversations, jobs, sdk_runner
from app.resume_context import is_continue
from app.sdk_errors import is_agent_busy, is_connect_stall, is_detached_run

logger = logging.getLogger(__name__)

CONTINUE_MSG = "继续"


def should_auto_resume(job: dict[str, Any] | None) -> bool:
    if not job or job.get("kind") != "chat" or job.get("status") != "failed":
        return False
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    payload = payload or {}
    if payload.get("auto_resume") or payload.get("auto_resume_of"):
        return False
    err = str(job.get("error") or "")
    if is_continue(str(payload.get("message") or "")) and not is_agent_busy(err):
        return False
    if not (is_connect_stall(err) or is_agent_busy(err) or is_detached_run(err)):
        return False
    cid = str(payload.get("conversation_id") or "").strip()
    if not cid:
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        cid = str((result or {}).get("conversation_id") or "").strip()
    return bool(cid)


def _conversation_id(job: dict[str, Any]) -> str:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    payload = payload or {}
    cid = str(payload.get("conversation_id") or "").strip()
    if cid:
        return cid
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    return str((result or {}).get("conversation_id") or "").strip()


def _has_active(cid: str, exclude_job_id: str) -> bool:
    skip = (exclude_job_id or "").strip()
    for row in conversations.conversation_jobs(cid):
        if str(row.get("job_id") or "") == skip:
            continue
        if row.get("status") in ("queued", "running"):
            return True
    return False


def kick_once(failed_job_id: str) -> str:
    job_id = (failed_job_id or "").strip()
    row = jobs.get_job(job_id) if job_id else None
    if not should_auto_resume(row):
        return ""
    assert row is not None
    cid = _conversation_id(row)
    if not cid or _has_active(cid, job_id):
        return ""
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    payload = payload or {}
    stored = {
        "message": CONTINUE_MSG,
        "conversation_id": cid,
        "mode": str(payload.get("mode") or "agent"),
        "model": str(payload.get("model") or ""),
        "model_params": list(payload.get("model_params") or []),
        "attachments": [],
        "auto_resume": True,
        "auto_resume_of": job_id,
    }
    nxt = jobs.create_job("chat", stored)
    nid = str(nxt["job_id"])
    conversations.attach_job(cid, nid)
    checkpoints.snapshot_turn(nid, cid, CONTINUE_MSG)
    why = "上一轮任务还在跑，自动再续 1 次" if is_agent_busy(row.get("error") or "") else "连接超时，自动断点续传 1 次"
    jobs.append_trace(job_id, "tool", "auto resume", why)
    jobs.patch_result(job_id, persist=True, auto_resume_job_id=nid)
    jobs.append_trace(nid, "tool", "auto resume", "自动从上一轮断点继续")
    mode = str(stored["mode"] or "agent")
    model = str(stored["model"] or "")
    params = list(stored["model_params"] or [])

    def _run() -> dict:
        try:
            sdk_runner.recover_bridge(nid)
        except Exception:
            logger.warning("recover_bridge before auto resume failed", exc_info=True)
        return sdk_runner.run_chat(
            CONTINUE_MSG,
            cid,
            job_id=nid,
            mode=mode,
            model=model,
            model_params=params,
        )

    jobs.start_background(nid, _run)
    return nid
