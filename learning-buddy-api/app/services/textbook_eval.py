"""Homework eval via the standalone textbook Agent HTTP API."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.clients.redis_client import enqueue_textbook_eval
from app.config import settings
from app.db.models import QuestionEntity, TextbookEvalRequest
from app.services.eval_util import apply_eval_to_question, parse_eval_blob
from app.kb.hint import pick_textbook_hint
from app.util.json_util import dumps

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def textbook_eval_enabled() -> bool:
    return bool((settings.textbook_agent_url or "").strip())


def enqueue_homework_for_eval(
    session: Session,
    *,
    student_id: str,
    homework_id: str,
    subject: str = "math",
    grade_band: str = "hs",
    force: bool = False,
) -> int:
    if not textbook_eval_enabled():
        return 0
    hint = pick_textbook_hint(session, subject=subject, grade_band=grade_band)
    questions = (
        session.query(QuestionEntity)
        .filter(
            QuestionEntity.homework_id == homework_id,
            QuestionEntity.student_id == student_id,
        )
        .order_by(QuestionEntity.ordinal)
        .all()
    )
    n = 0
    now = _now_iso()
    for q in questions:
        ev = parse_eval_blob(q.eval_json)
        if not force and ev.get("eval_source") == "cursor_agent" and ev.get("eval_status") in {
            "pending_textbook",
            "completed",
        }:
            continue
        pending = (
            session.query(TextbookEvalRequest)
            .filter(
                TextbookEvalRequest.question_id == q.id,
                TextbookEvalRequest.status == "pending",
            )
            .first()
        )
        if pending is not None and not force:
            continue
        request_id = f"lb-{q.id}"
        row = session.get(TextbookEvalRequest, request_id)
        if row is None:
            row = TextbookEvalRequest(request_id=request_id)
            session.add(row)
        row.question_id = q.id
        row.homework_id = homework_id
        row.student_id = student_id
        row.status = "pending"
        row.created_at = row.created_at or now
        row.updated_at = now
        prev = parse_eval_blob(q.eval_json)
        prev.update(
            {
                "eval_source": "cursor_agent",
                "eval_status": "pending_textbook",
                "textbook_hint": hint,
            }
        )
        q.eval_json = dumps(prev)
        session.flush()
        enqueue_textbook_eval(request_id)
        n += 1
    return n


def build_grade_payload(session: Session, request_id: str) -> dict[str, Any]:
    row = session.get(TextbookEvalRequest, request_id)
    if row is None:
        raise ValueError(f"eval request not found: {request_id}")
    q = session.get(QuestionEntity, row.question_id)
    if q is None:
        raise ValueError(f"question not found: {row.question_id}")
    ev = parse_eval_blob(q.eval_json)
    hint = str(ev.get("textbook_hint") or pick_textbook_hint(session, q.subject, q.grade_band))
    return {
        "request_id": request_id,
        "subject": q.subject or "math",
        "grade": q.grade_band or "hs",
        "textbook_hint": hint,
        "problem_text": q.print_text or "",
        "student_answer": q.handwriting or "",
    }


def call_textbook_agent(session: Session, request_id: str) -> None:
    base = (settings.textbook_agent_url or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("TEXTBOOK_AGENT_URL is empty")
    payload = build_grade_payload(session, request_id)
    headers = {"Content-Type": "application/json"}
    token = (settings.textbook_agent_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = settings.textbook_agent_timeout_s
    with httpx.Client(timeout=min(30.0, timeout)) as client:
        r = client.post(f"{base}/v1/grade", json=payload, headers=headers)
        r.raise_for_status()
        job_id = str(r.json().get("job_id") or "")
        if not job_id:
            raise RuntimeError("textbook agent returned no job_id")
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = client.get(f"{base}/v1/jobs/{job_id}", headers=headers)
            j.raise_for_status()
            body = j.json()
            status = body.get("status")
            if status == "completed":
                result = body.get("result") or {}
                if not isinstance(result, dict):
                    raise RuntimeError("textbook agent result is not an object")
                result.setdefault("request_id", request_id)
                apply_eval_callback(session, result)
                return
            if status == "failed":
                raise RuntimeError(str(body.get("error") or "textbook agent failed"))
            time.sleep(2)
    raise TimeoutError(f"textbook agent timed out request_id={request_id}")


def apply_eval_callback(session: Session, body: dict[str, Any]) -> dict[str, Any]:
    request_id = str(body.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("request_id required")
    row = session.get(TextbookEvalRequest, request_id)
    if row is None:
        raise ValueError(f"unknown request_id: {request_id}")
    q = session.get(QuestionEntity, row.question_id)
    if q is None:
        raise ValueError(f"question not found: {row.question_id}")

    book_missing = bool(body.get("book_missing"))
    correct = body.get("correct")
    is_wrong: bool | None
    if book_missing or correct is None:
        is_wrong = None
    else:
        is_wrong = not bool(correct)

    ev: dict[str, Any] = {
        "eval_source": "cursor_agent",
        "eval_status": "completed",
        "is_wrong": is_wrong,
        "verdict": body.get("verdict") or "",
        "reference_answer": body.get("correct_solution") or "",
        "match_notes": body.get("notes") or "",
        "textbook": body.get("textbook") if isinstance(body.get("textbook"), dict) else {},
        "book_missing": book_missing,
        "eval_confidence": 0.0 if book_missing or is_wrong is None else 0.99,
    }
    apply_eval_to_question(q, ev)
    blob = parse_eval_blob(q.eval_json)
    blob["prompt_version"] = "cursor_textbook_eval_v1"
    blob["eval_source"] = "cursor_agent"
    blob["verdict"] = ev["verdict"]
    blob["textbook"] = ev["textbook"]
    blob["book_missing"] = book_missing
    q.eval_json = dumps(blob)
    row.status = "completed"
    row.updated_at = _now_iso()
    session.flush()
    return {"request_id": request_id, "question_id": q.id, "status": "completed"}


def mark_eval_failed(session: Session, request_id: str, error: str) -> None:
    row = session.get(TextbookEvalRequest, request_id)
    if row is None:
        return
    row.status = "failed"
    row.updated_at = _now_iso()
    q = session.get(QuestionEntity, row.question_id)
    if q is not None:
        ev = parse_eval_blob(q.eval_json)
        ev["eval_source"] = "cursor_agent"
        ev["eval_status"] = "failed"
        ev["match_notes"] = (error or "textbook agent failed")[:500]
        q.eval_json = dumps(ev)
    session.flush()
