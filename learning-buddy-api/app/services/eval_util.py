"""Eval JSON parsing, Agent vs manual wrong-book policy, candidate detection."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.db.models import QuestionEntity
from app.util.json_util import dumps, loads

PROMPT_VERSION = "eval_v2"
WrongSource = str | None  # eval | manual | pending | None


def parse_eval_blob(raw: str | dict | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        data = loads(raw, {})
    else:
        data = dict(raw)
    if not isinstance(data, dict):
        return {}
    return data


def eval_fields_from_question(q: QuestionEntity) -> dict[str, Any]:
    ev = parse_eval_blob(q.eval_json)
    agent = ev.get("agent_is_wrong")
    if agent is None and "is_wrong" in ev and ev.get("eval_status") == "completed":
        agent = ev.get("is_wrong")
    return {
        "eval_json": ev,
        "agent_is_wrong": agent,
        "wrong_source": ev.get("wrong_source"),
        "eval_status": ev.get("eval_status") or ("completed" if ev.get("reference_answer") else ("failed" if ev else "pending")),
        "reference_answer": ev.get("reference_answer"),
        "match_notes": ev.get("match_notes"),
        "error_type": ev.get("error_type"),
        "manual_override": bool(ev.get("manual_override")),
    }


def apply_eval_to_question(q: QuestionEntity, ev: dict[str, Any]) -> dict[str, Any]:
    """Merge Agent eval into question; respect manual_override."""
    prev = parse_eval_blob(q.eval_json)
    manual = bool(prev.get("manual_override"))

    ev = dict(ev)
    ev["prompt_version"] = PROMPT_VERSION
    ev["eval_status"] = ev.get("eval_status") or "completed"
    agent = ev.get("is_wrong")
    if agent is not None and not isinstance(agent, bool):
        agent = None
    ev["agent_is_wrong"] = agent

    conf = ev.get("eval_confidence")
    if conf is not None:
        q.eval_confidence = float(conf)

    if not manual:
        threshold = settings.eval_confidence_threshold
        if agent is True and conf is not None and float(conf) >= threshold:
            q.is_wrong = True
            ev["wrong_source"] = "eval"
        elif agent is False and conf is not None and float(conf) >= threshold:
            q.is_wrong = False
            ev["wrong_source"] = "eval"
        elif agent is None:
            q.is_wrong = None
            ev["wrong_source"] = "pending"
        else:
            ev["wrong_source"] = "pending"
    else:
        ev["manual_override"] = True
        ev["wrong_source"] = "manual"

    q.eval_json = dumps(ev)
    return ev


def mark_eval_failed(q: QuestionEntity, *, error: str = "") -> None:
    prev = parse_eval_blob(q.eval_json)
    prev.update({"eval_status": "failed", "prompt_version": PROMPT_VERSION})
    if error:
        prev["eval_error"] = error[:240]
    q.eval_json = dumps(prev)


def merge_manual_patch(q: QuestionEntity, *, is_wrong: bool | None, restore_agent: bool = False) -> None:
    ev = parse_eval_blob(q.eval_json)

    if restore_agent:
        ev.pop("manual_override", None)
        agent = ev.get("agent_is_wrong")
        conf = ev.get("eval_confidence")
        threshold = settings.eval_confidence_threshold
        if agent is True and conf is not None and float(conf) >= threshold:
            q.is_wrong = True
            ev["wrong_source"] = "eval"
        elif agent is False and conf is not None and float(conf) >= threshold:
            q.is_wrong = False
            ev["wrong_source"] = "eval"
        elif agent is None:
            q.is_wrong = None
            ev["wrong_source"] = "pending"
        q.eval_json = dumps(ev)
        return

    if is_wrong is not None:
        q.is_wrong = is_wrong
        ev["manual_override"] = True
        ev["wrong_source"] = "manual"
        q.eval_json = dumps(ev)


def is_wrong_book_candidate(q: QuestionEntity) -> bool:
    if q.is_wrong is True:
        return False
    if not (q.print_text or "").strip():
        return False
    fields = eval_fields_from_question(q)
    has_hw = bool((q.handwriting or "").strip())
    status = fields.get("eval_status")
    agent = fields.get("agent_is_wrong")
    conf = q.eval_confidence

    if status == "failed" and has_hw:
        return True
    if status == "pending" and has_hw:
        return True
    if agent is None and has_hw:
        return True
    if agent is False and conf is not None and float(conf) < settings.eval_confidence_threshold and has_hw:
        return True
    return False


def candidate_reason(q: QuestionEntity) -> str:
    fields = eval_fields_from_question(q)
    status = fields.get("eval_status")
    agent = fields.get("agent_is_wrong")
    conf = q.eval_confidence
    ev = fields.get("eval_json") or {}
    if status == "failed":
        return "eval_failed"
    if status == "pending":
        return "eval_pending"
    if agent is None:
        return "agent_uncertain"
    if agent is False and conf is not None and float(conf) < settings.eval_confidence_threshold:
        return "low_confidence_correct"
    return "review"
