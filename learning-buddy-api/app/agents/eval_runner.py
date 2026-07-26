from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.agents.eval_agent import evaluate_question_full
from app.agents.eval_bank import lookup_bank_answer
from app.agents.eval_file_logger import append_eval_trace, ensure_eval_log_dir, today_log_basename
from app.config import settings
from app.db.models import HomeworkEntry, MemoryEvent, QuestionEntity, Student
from app.math.enrich import enrich_eval_with_rich
from app.rag.kp_resolver import resolve_kp_label
from app.rag.retriever import retrieve_eval_context
from app.rag.solve_hints import build_solve_hints
from app.services.eval_util import apply_eval_to_question, mark_eval_failed, parse_eval_blob
from app.util.json_util import dumps

logger = logging.getLogger(__name__)


def _rag_meta(ctx) -> dict[str, Any]:
    return {
        "entry_node_ids": list(ctx.entry_node_ids or []),
        "hit_count": len(ctx.hits or []),
        "kp_count": len(ctx.kps or []),
        "method_count": len(ctx.methods or []),
        "shortcut_count": len(ctx.shortcuts or []),
        "chunk_count": len(ctx.chunks or []),
        "question_count": len(ctx.questions or []),
    }


def run_eval_on_homework(
    session: Session,
    student_id: str,
    homework_id: str,
    subject: str,
    grade_band: str,
    *,
    clear_manual_override: bool = False,
    stats: dict[str, Any] | None = None,
) -> int:
    from app.services.monetization import has_feature

    ensure_eval_log_dir()

    st = session.get(Student, student_id)
    if st and not has_feature(st, "eval"):
        logger.info("eval skipped: tier lacks eval feature student=%s", student_id)
        return 0
    rows = (
        session.query(QuestionEntity)
        .filter(
            QuestionEntity.homework_id == homework_id,
            QuestionEntity.student_id == student_id,
        )
        .all()
    )
    updated = 0
    trace_ms: list[int] = []
    trace_count = 0
    for q in rows:
        if not (q.print_text or "").strip():
            continue
        if clear_manual_override:
            ev = parse_eval_blob(q.eval_json)
            ev.pop("manual_override", None)
            q.eval_json = dumps(ev) if ev else None

        ctx = retrieve_eval_context(q.print_text, subject=subject, grade_band=grade_band)
        hints = build_solve_hints(ctx)
        bank_ans, bank_src, bank_score = (None, "", 0.0)
        if settings.eval_use_bank_answer:
            bank_ans, bank_src, bank_score = lookup_bank_answer(q.print_text, ctx)

        ev = evaluate_question_full(
            print_text=q.print_text,
            handwriting=q.handwriting or "",
            hints_text=hints.hints_text,
            hints_meta=hints.hints_meta,
            rag_meta=_rag_meta(ctx),
            subject=subject,
            grade_band=grade_band,
            reference_answer=bank_ans,
            bank_source=bank_src,
            bank_stem_score=bank_score,
        )
        if not ev:
            mark_eval_failed(q, error="solve_or_judge_failed")
            updated += 1
            continue

        trace = ev.get("eval_trace")
        if isinstance(trace, dict):
            trace_count += 1
            if trace.get("total_ms") is not None:
                trace_ms.append(int(trace["total_ms"]))
            append_eval_trace(
                student_id=student_id,
                homework_id=homework_id,
                question_id=q.id,
                eval_trace=trace,
                eval_result=ev,
            )

        ev = enrich_eval_with_rich(ev)
        if ev.get("kp_id"):
            kid = str(ev["kp_id"])
            q.kp_tags_json = dumps(
                [
                    {
                        "kp_id": kid,
                        "score": float(ev.get("kp_confidence", 0.7)),
                        "source": "eval_agent",
                        "label": resolve_kp_label(kid, session),
                    }
                ]
            )
        ev = apply_eval_to_question(q, ev)
        session.add(
            MemoryEvent(
                student_id=student_id,
                session_id="",
                ts=q.created_at,
                type="EVAL_AGENT_NOTE",
                question_id=q.id,
                kp_id=ev.get("kp_id"),
                payload_json=dumps({"eval": ev}),
            )
        )
        if ev.get("eval_confidence") is not None and float(ev["eval_confidence"]) >= settings.eval_confidence_threshold:
            session.add(
                MemoryEvent(
                    student_id=student_id,
                    session_id="",
                    ts=q.created_at,
                    type="QUESTION_EVALUATED",
                    question_id=q.id,
                    kp_id=ev.get("kp_id"),
                    payload_json=dumps({"eval": ev}),
                )
            )
        updated += 1
    session.flush()

    if stats is not None:
        avg_ms = int(sum(trace_ms) / len(trace_ms)) if trace_ms else 0
        stats.update(
            {
                "eval_updated": updated,
                "traces": trace_count,
                "avg_ms": avg_ms,
                "log_file": today_log_basename(),
            }
        )

    return updated


def run_eval_on_homework_ids(
    session: Session,
    student_id: str,
    homework_ids: list[str],
    *,
    clear_manual_override: bool = False,
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for hw_id in homework_ids:
        entry = session.get(HomeworkEntry, hw_id)
        if entry is None or entry.student_id != student_id:
            continue
        meta = entry.meta_json or "{}"
        from app.util.json_util import loads

        meta_d = loads(meta, {})
        subject = entry.subject or meta_d.get("subject", "math")
        grade_band = entry.grade_band or meta_d.get("grade_band", "hs")
        n = run_eval_on_homework(
            session,
            student_id,
            hw_id,
            subject,
            grade_band,
            clear_manual_override=clear_manual_override,
        )
        totals[hw_id] = n
    return totals


def distinct_homework_ids_for_wrong_book(session: Session, student_id: str) -> list[str]:
    from app.services.wrong_book import build_wrong_book

    wb = build_wrong_book(session, student_id)
    ids: set[str] = set()
    for item in wb.items + wb.candidates:
        if item.homework_id:
            ids.add(item.homework_id)
    return sorted(ids)
