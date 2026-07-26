"""Append eval traces to daily .log and .jsonl under logs/eval/."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_TRUNC = 2000


def ensure_eval_log_dir() -> Path:
    path = settings.eval_log_path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def today_log_basename() -> str:
    return f"eval-{_today()}.log"


def _log_path() -> Path:
    return ensure_eval_log_dir() / f"eval-{_today()}.log"


def _jsonl_path() -> Path:
    return ensure_eval_log_dir() / f"eval-{_today()}.jsonl"


def _trunc(text: str, limit: int = _TRUNC) -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[:limit] + f"... [truncated {len(s) - limit} chars]"


def append_eval_trace(
    *,
    student_id: str,
    homework_id: str,
    question_id: str,
    eval_trace: dict[str, Any],
    eval_result: dict[str, Any] | None = None,
) -> None:
    if not settings.eval_log_to_file:
        return
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        "ts": ts,
        "student_id": student_id,
        "homework_id": homework_id,
        "question_id": question_id,
        "eval_trace": eval_trace,
    }
    if eval_result:
        row["reference_answer"] = eval_result.get("reference_answer")
        row["is_wrong"] = eval_result.get("is_wrong")
        row["eval_confidence"] = eval_result.get("eval_confidence")

    with _lock:
        try:
            with _jsonl_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _append_human_log(
                ts=ts,
                student_id=student_id,
                homework_id=homework_id,
                question_id=question_id,
                eval_trace=eval_trace,
                eval_result=eval_result,
            )
        except OSError as e:
            logger.warning("eval file log write failed: %s", e)


def _append_human_log(
    *,
    ts: str,
    student_id: str,
    homework_id: str,
    question_id: str,
    eval_trace: dict[str, Any],
    eval_result: dict[str, Any] | None,
) -> None:
    lines: list[str] = []
    total_ms = eval_trace.get("total_ms", "?")
    lines.append(
        f"=== eval {ts} question={question_id} homework={homework_id} "
        f"student={student_id} ms={total_ms} ==="
    )
    for stage in eval_trace.get("stages") or []:
        name = stage.get("stage", "?")
        ms = stage.get("ms", "?")
        out = stage.get("output") or {}
        extra = ""
        if name == "solve_hints":
            extra = f" hints_len={out.get('hints_len', len(str(out.get('hints_text', ''))))}"
        elif name == "solve_think":
            extra = (
                f" thinking=ON reasoning_len={out.get('reasoning_len', '?')}"
                f" content_len={out.get('content_len', '?')}"
            )
        elif name == "solve_format":
            extra = f" reference={out.get('reference_answer', out.get('parsed', ''))}"
        elif name == "judge":
            extra = f" is_wrong={out.get('is_wrong')} conf={out.get('eval_confidence')}"
        elif name == "apply_eval":
            extra = f" wrong_source={out.get('wrong_source')}"
        lines.append(f"[{name}] {ms}ms{extra}")

    if eval_result:
        lines.append(
            f"RESULT reference={eval_result.get('reference_answer')} "
            f"is_wrong={eval_result.get('is_wrong')} conf={eval_result.get('eval_confidence')}"
        )

    for stage in eval_trace.get("stages") or []:
        if stage.get("stage") != "solve_think":
            continue
        out = stage.get("output") or {}
        reasoning = out.get("reasoning_content") or ""
        content = out.get("content") or ""
        if reasoning:
            lines.append("--- solve_think reasoning ---")
            lines.append(_trunc(reasoning))
        if content:
            lines.append("--- solve_think content ---")
            lines.append(_trunc(content))

    lines.append("")
    with _log_path().open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
