from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import HomeworkEntry, MemoryEvent, MasteryView, QuestionEntity, Student
from app.jobs.queue import schedule_graph_run
from app.rag.corpus.pipeline import kp_label
from app.rag.retriever import map_question_to_kp
from app.schemas.common import HomeworkOut, IngestHomeworkRequest, MemoryEventIn, QuestionOut
from app.services.aplus_aggregate import recompute_for_kps, recompute_student
from app.services.corpus_graph import link_question_from_ingest
from app.clients.redis_client import enqueue_edge_completion
from app.util.json_util import dumps, loads
from pathlib import Path
from app.config import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _meta_subject_grade(meta: dict) -> tuple[str, str]:
    subject = str(meta.get("subject") or "math").strip().lower()
    grade_band = str(meta.get("grade_band") or meta.get("grade") or "hs").strip().lower()
    return subject, grade_band


def _corpus_available(subject: str, grade_band: str) -> bool:
    corpus = Path(settings.corpus_dir)
    syl = corpus / "syllabus" / f"{subject}_{grade_band}.json"
    bank = corpus / "question-bank" / f"{subject}_{grade_band}.jsonl"
    return syl.is_file() and bank.is_file()


def _ensure_student(session: Session, student_id: str) -> Student:
    st = session.get(Student, student_id)
    if st is None or not st.active:
        raise HTTPException(status_code=404, detail=f"Student not found: {student_id}")
    return st


def _emit_event(
    session: Session,
    student_id: str,
    ev: MemoryEventIn,
) -> MemoryEvent:
    row = MemoryEvent(
        student_id=student_id,
        session_id=ev.session_id,
        ts=ev.ts,
        type=ev.type,
        question_id=ev.question_id,
        kp_id=ev.kp_id,
        payload_json=dumps(ev.payload),
    )
    session.add(row)
    return row


def ingest_events(session: Session, student_id: str, events: list[MemoryEventIn]) -> int:
    _ensure_student(session, student_id)
    for ev in events:
        _emit_event(session, student_id, ev)
    session.flush()
    return len(events)


def ingest_homework(
    session: Session,
    student_id: str,
    body: IngestHomeworkRequest,
) -> HomeworkOut:
    _ensure_student(session, student_id)
    now = _now_iso()
    subject, grade_band = _meta_subject_grade(body.meta)
    if not _corpus_available(subject, grade_band):
        raise HTTPException(
            status_code=422,
            detail=f"Corpus not available for subject={subject} grade_band={grade_band}",
        )

    entry = HomeworkEntry(
        id=body.id,
        student_id=student_id,
        session_id=body.session_id,
        page_id=body.page_id,
        subject=subject,
        grade_band=grade_band,
        finalized_at=body.finalized_at,
        source_frame_ids_json=dumps(body.source_frame_ids),
        meta_json=dumps(body.meta),
    )
    session.merge(entry)

    affected_kps: set[str] = set()
    questions_out: list[QuestionOut] = []
    timings_by_q: dict[str, dict] = {}
    for t in body.meta.get("question_timings") or []:
        qid = str(t.get("question_id", ""))
        if qid:
            timings_by_q[qid] = t

    for part in body.parts:
        if part.kind != "question" or not part.id:
            continue
        kp_tags, evidence = map_question_to_kp(
            part.print_text or "",
            subject=subject,
            grade_band=grade_band,
        )
        qid = f"{body.id}__q__{part.id}"
        timing = timings_by_q.get(str(part.id), {})
        dwell_ms = timing.get("duration_ms")
        q = QuestionEntity(
            id=qid,
            student_id=student_id,
            homework_id=body.id,
            ordinal=str(part.id),
            subject=subject,
            grade_band=grade_band,
            print_text=part.print_text or "",
            handwriting=part.handwriting or "",
            bbox_print_json=dumps(part.bbox_print),
            bbox_handwriting_json=dumps(part.bbox_handwriting),
            kp_tags_json=dumps(kp_tags),
            rag_evidence_json=dumps(evidence),
            dwell_ms=int(dwell_ms) if dwell_ms is not None else None,
            created_at=now,
        )
        session.merge(q)
        questions_out.append(question_to_out(q))

        kp_ids = [t["kp_id"] for t in kp_tags if t.get("kp_id")]
        link_question_from_ingest(
            session,
            question_node_id=qid,
            kp_ids=kp_ids,
            subject=subject,
            grade_band=grade_band,
            source="ingest",
        )
        enqueue_edge_completion(qid, subject, grade_band)

        tag_ev = MemoryEventIn(
            session_id=body.session_id,
            ts=now,
            type="QUESTION_TAGGED",
            question_id=q.id,
            kp_id=kp_tags[0]["kp_id"] if kp_tags else None,
            payload={"kp_tags": kp_tags, "ordinal": part.id, "subject": subject, "grade_band": grade_band},
        )
        _emit_event(session, student_id, tag_ev)
        if dwell_ms is not None:
            _emit_event(
                session,
                student_id,
                MemoryEventIn(
                    session_id=body.session_id,
                    ts=now,
                    type="QUESTION_DWELL",
                    question_id=q.id,
                    kp_id=kp_tags[0]["kp_id"] if kp_tags else None,
                    payload={
                        "duration_ms": dwell_ms,
                        "duration_source": timing.get("duration_source"),
                        "first_answer_at": timing.get("first_answer_at"),
                    },
                ),
            )
        for t in kp_tags:
            affected_kps.add(t["kp_id"])

    fin_ev = MemoryEventIn(
        session_id=body.session_id,
        ts=now,
        type="PAGE_FINALIZED",
        payload={
            "homework_id": body.id,
            "question_count": len(questions_out),
            "subject": subject,
            "grade_band": grade_band,
        },
    )
    _emit_event(session, student_id, fin_ev)
    session.flush()

    if affected_kps:
        recompute_for_kps(session, student_id, affected_kps, subject=subject, grade_band=grade_band)
    else:
        recompute_student(session, student_id)

    schedule_graph_run(student_id, body.id, subject=subject, grade_band=grade_band)

    return homework_to_out(entry, questions_out, body.parts)


def question_to_out(q: QuestionEntity) -> QuestionOut:
    return QuestionOut(
        id=q.id,
        homework_id=q.homework_id,
        ordinal=q.ordinal,
        print_text=q.print_text,
        handwriting=q.handwriting,
        bbox_print=loads(q.bbox_print_json, []),
        bbox_handwriting=loads(q.bbox_handwriting_json, []),
        dwell_ms=q.dwell_ms,
        eval_confidence=q.eval_confidence,
        is_wrong=q.is_wrong,
        kp_tags=loads(q.kp_tags_json, []),
        rag_evidence=loads(q.rag_evidence_json, []),
        created_at=q.created_at,
    )


def homework_to_out(
    entry: HomeworkEntry,
    questions: list[QuestionOut] | None = None,
    parts=None,
) -> HomeworkOut:
    return HomeworkOut(
        id=entry.id,
        student_id=entry.student_id,
        session_id=entry.session_id,
        page_id=entry.page_id,
        finalized_at=entry.finalized_at,
        source_frame_ids=loads(entry.source_frame_ids_json, []),
        parts=parts or [],
        meta=loads(entry.meta_json, {}),
        questions=questions or [],
    )


def get_homework_detail(session: Session, student_id: str, homework_id: str) -> HomeworkOut | None:
    entry = session.get(HomeworkEntry, homework_id)
    if entry is None or entry.student_id != student_id:
        return None
    qs = (
        session.query(QuestionEntity)
        .filter(
            QuestionEntity.homework_id == homework_id,
            QuestionEntity.student_id == student_id,
        )
        .order_by(QuestionEntity.ordinal)
        .all()
    )
    return homework_to_out(entry, [question_to_out(q) for q in qs])


def list_homework(session: Session, student_id: str) -> list[HomeworkOut]:
    rows = (
        session.query(HomeworkEntry)
        .filter(HomeworkEntry.student_id == student_id)
        .order_by(HomeworkEntry.finalized_at.desc())
        .all()
    )
    out: list[HomeworkOut] = []
    for entry in rows:
        qs = (
            session.query(QuestionEntity)
            .filter(QuestionEntity.homework_id == entry.id)
            .all()
        )
        out.append(homework_to_out(entry, [question_to_out(q) for q in qs]))
    return out


def patch_question(
    session: Session,
    student_id: str,
    question_id: str,
    *,
    is_wrong: bool | None = None,
    kp_tags: list | None = None,
) -> QuestionOut | None:
    q = session.get(QuestionEntity, question_id)
    if q is None or q.student_id != student_id:
        return None
    now = _now_iso()
    affected: set[str] = set()

    if is_wrong is not None:
        q.is_wrong = is_wrong
        if is_wrong:
            _emit_event(
                session,
                student_id,
                MemoryEventIn(
                    session_id="",
                    ts=now,
                    type="MARKED_WRONG",
                    question_id=question_id,
                    payload={"ordinal": q.ordinal},
                ),
            )

    if kp_tags is not None:
        q.kp_tags_json = dumps([t.model_dump() if hasattr(t, "model_dump") else t for t in kp_tags])
        for t in kp_tags:
            kid = t.kp_id if hasattr(t, "kp_id") else t.get("kp_id")
            if kid:
                affected.add(kid)

    session.flush()
    for t in loads(q.kp_tags_json, []):
        affected.add(t.get("kp_id", ""))
    affected.discard("")

    if affected:
        recompute_for_kps(
            session,
            student_id,
            affected,
            subject=q.subject,
            grade_band=q.grade_band,
        )
    else:
        recompute_student(session, student_id)

    return question_to_out(q)


def list_mastery(session: Session, student_id: str) -> list[MasteryView]:
    return (
        session.query(MasteryView)
        .filter(MasteryView.student_id == student_id)
        .order_by(MasteryView.updated_at.desc())
        .all()
    )


def mastery_to_out(m: MasteryView) -> dict:
    return {
        "student_id": m.student_id,
        "kp_id": m.kp_id,
        "label": m.label or kp_label(m.kp_id),
        "mastery": m.mastery,
        "confidence": m.confidence,
        "weak": m.weak,
        "fluency": m.fluency,
        "fluency_ratio": m.fluency_ratio,
        "wrong_rate": m.wrong_rate,
        "evidence": loads(m.evidence_json, []),
        "updated_at": m.updated_at,
        "demo": m.demo,
        "subject": m.subject,
        "grade_band": m.grade_band,
    }
