from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Student(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    api_token_hash: Mapped[str] = mapped_column(String(128), default="")
    tier: Mapped[str] = mapped_column(String(32), default="trial")
    trial_started_at: Mapped[str] = mapped_column(String(64), default="")
    trial_expires_at: Mapped[str] = mapped_column(String(64), default="")
    org_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    homework_entries: Mapped[list[HomeworkEntry]] = relationship(back_populates="student")
    memory_events: Mapped[list[MemoryEvent]] = relationship(back_populates="student")
    mastery_views: Mapped[list[MasteryView]] = relationship(back_populates="student")


class HomeworkEntry(Base):
    __tablename__ = "homework_entries"
    __table_args__ = (Index("ix_hw_student_subject", "student_id", "subject", "grade_band"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    page_id: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    finalized_at: Mapped[str] = mapped_column(String(64))
    source_frame_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")

    student: Mapped[Student] = relationship(back_populates="homework_entries")
    questions: Mapped[list[QuestionEntity]] = relationship(
        back_populates="homework", cascade="all, delete-orphan"
    )


class QuestionEntity(Base):
    __tablename__ = "question_entities"
    __table_args__ = (Index("ix_q_student_subject", "student_id", "subject", "grade_band"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(String(64), index=True)
    homework_id: Mapped[str] = mapped_column(ForeignKey("homework_entries.id"), index=True)
    ordinal: Mapped[str] = mapped_column(String(32), default="")
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    print_text: Mapped[str] = mapped_column(Text, default="")
    handwriting: Mapped[str] = mapped_column(Text, default="")
    bbox_print_json: Mapped[str] = mapped_column(Text, default="[]")
    bbox_handwriting_json: Mapped[str] = mapped_column(Text, default="[]")
    is_wrong: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    eval_confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    eval_json: Mapped[str] = mapped_column(Text, default="{}")
    dwell_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    kp_tags_json: Mapped[str] = mapped_column(Text, default="[]")
    rag_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[str] = mapped_column(String(64), default="")

    homework: Mapped[HomeworkEntry] = relationship(back_populates="questions")


class MemoryEvent(Base):
    __tablename__ = "memory_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    ts: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kp_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    student: Mapped[Student] = relationship(back_populates="memory_events")


class MasteryView(Base):
    __tablename__ = "mastery_views"

    student_id: Mapped[str] = mapped_column(ForeignKey("students.id"), primary_key=True)
    kp_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    label: Mapped[str] = mapped_column(String(256), default="")
    mastery: Mapped[str] = mapped_column(String(32), default="unknown")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    weak: Mapped[bool] = mapped_column(Boolean, default=False)
    fluency: Mapped[float] = mapped_column(Float, default=0.0)
    fluency_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    wrong_rate: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    updated_at: Mapped[str] = mapped_column(String(64), default="")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)

    student: Mapped[Student] = relationship(back_populates="mastery_views")


class CorpusNode(Base):
    __tablename__ = "corpus_nodes"
    __table_args__ = (Index("ix_corpus_node_type_scope", "node_type", "subject", "grade_band"),)

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    label: Mapped[str] = mapped_column(String(512), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    full_text: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="published")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusEdge(Base):
    __tablename__ = "corpus_edges"
    __table_args__ = (
        Index("ix_corpus_edge_from", "from_id", "rel_type"),
        Index("ix_corpus_edge_to", "to_id", "rel_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    from_id: Mapped[str] = mapped_column(String(256), index=True)
    to_id: Mapped[str] = mapped_column(String(256), index=True)
    rel_type: Mapped[str] = mapped_column(String(64), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusTagLink(Base):
    __tablename__ = "corpus_tag_links"

    node_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    tag_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")


class GraphRun(Base):
    __tablename__ = "graph_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(String(64), index=True)
    homework_id: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    status: Mapped[str] = mapped_column(String(32), default="running")
    steps_json: Mapped[str] = mapped_column(Text, default="[]")
    started_at: Mapped[str] = mapped_column(String(64), default="")
    finished_at: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
