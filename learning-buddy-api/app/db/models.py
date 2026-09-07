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
    print_segments_json: Mapped[str] = mapped_column(Text, default="[]")
    handwriting_segments_json: Mapped[str] = mapped_column(Text, default="[]")
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
    status: Mapped[str] = mapped_column(String(32), default="draft")
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


class CorpusImportJob(Base):
    __tablename__ = "corpus_import_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    source_channel: Mapped[str] = mapped_column(String(32), default="pdf_import")
    minio_key: Mapped[str] = mapped_column(String(512), default="")
    markdown: Mapped[str] = mapped_column(Text, default="")
    textbook_node_id: Mapped[str] = mapped_column(String(256), default="")
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    label: Mapped[str] = mapped_column(String(512), default="")
    edition_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusScoutRun(Base):
    __tablename__ = "corpus_scout_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    query: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    steps_json: Mapped[str] = mapped_column(Text, default="[]")
    inbox_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[str] = mapped_column(String(64), default="")
    finished_at: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class CorpusInboxItem(Base):
    __tablename__ = "corpus_inbox_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    scout_run_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    source_platform: Mapped[str] = mapped_column(String(64), default="")
    resource_kind: Mapped[str] = mapped_column(String(32), default="article")
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    title: Mapped[str] = mapped_column(String(512), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    proposed_node_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    agent_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    agent_notes_json: Mapped[str] = mapped_column(Text, default="{}")
    reviewer: Mapped[str] = mapped_column(String(128), default="")
    reviewed_at: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")


# ---------------------------------------------------------------------------
# 35B import line — FULLY ISOLATED storage.
#
# These tables belong ONLY to the 35B layout import pipeline. They share NO
# rows, foreign keys or joins with corpus_nodes / corpus_import_jobs (the
# MinerU line). The only cross-line dependency in the whole 35B pipeline is
# the 35B vLLM model service. Do NOT add relationships to corpus tables here.
# ---------------------------------------------------------------------------
class Import35bJob(Base):
    __tablename__ = "import35b_jobs"
    __table_args__ = (Index("ix_import35b_job_key", "textbook_key", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    textbook_key: Mapped[str] = mapped_column(String(256), index=True, default="")
    label: Mapped[str] = mapped_column(String(512), default="")
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    minio_key: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    page_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    profile_id: Mapped[str] = mapped_column(String(64), default="hujiao_math_hs_v1")
    error: Mapped[str] = mapped_column(Text, default="")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class Import35bPage(Base):
    __tablename__ = "import35b_pages"
    __table_args__ = (Index("ix_import35b_page_job", "job_id"),)

    textbook_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    html: Mapped[str] = mapped_column(Text, default="")
    plain_text: Mapped[str] = mapped_column(Text, default="")
    quality_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class Import35bReviewItem(Base):
    """v4 校验面 finding → HITL 审核队列（35B 自有表，禁止跨线 join）。"""

    __tablename__ = "import35b_review_items"
    __table_args__ = (Index("ix_import35b_review_job", "job_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    textbook_key: Mapped[str] = mapped_column(String(256), index=True, default="")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    block_id: Mapped[str] = mapped_column(String(128), default="")
    suspect_type: Mapped[str] = mapped_column(String(64), index=True, default="")
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    candidates_json: Mapped[str] = mapped_column(Text, default="[]")
    fingerprint: Mapped[str] = mapped_column(String(32), index=True, default="")
    fingerprint_exact: Mapped[str] = mapped_column(String(32), index=True, default="")
    # open | confirmed | false_positive | corrected | reported
    status: Mapped[str] = mapped_column(String(32), index=True, default="open")
    verdict_json: Mapped[str] = mapped_column(Text, default="{}")
    # rule | agent | user_report
    detector: Mapped[str] = mapped_column(String(32), default="rule")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class Import35bSuppression(Base):
    """误报抑制（「这不是一个错误」）：exact=仅此处，fingerprint=同指纹。"""

    __tablename__ = "import35b_suppressions"
    __table_args__ = (Index("ix_import35b_suppr_fp", "fingerprint"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # exact | fingerprint （「规则太严」只生成提议，不落此表）
    scope: Mapped[str] = mapped_column(String(16), default="exact")
    fingerprint: Mapped[str] = mapped_column(String(32), index=True, default="")
    suspect_type: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")


class Import35bOverride(Base):
    """用户修正值叠加层：确定性重编译时应用，可重放可撤销。"""

    __tablename__ = "import35b_overrides"
    __table_args__ = (Index("ix_import35b_override_page", "textbook_key", "page_no"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    textbook_key: Mapped[str] = mapped_column(String(256), index=True, default="")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    block_id: Mapped[str] = mapped_column(String(128), default="")
    # latex | text — 目标字段类型
    target: Mapped[str] = mapped_column(String(32), default="latex")
    # {"old": "...", "new": "..."} 旧值精确匹配后替换
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    review_item_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")


class Import35bCaseLaw(Base):
    """v4 判例库 canonical 存储（35B 自有表，禁止跨线 join）。

    「判例法」两速记忆中的慢速层：人对 finding 的每次裁决沉淀为一条判例——
    误判库（``kind=misjudgment``，来自 false_positive 裁决）与方案库
    （``kind=solution``，来自 correct 裁决）。此表是唯一事实来源；向量索引
    落在独立 Milvus collection（``import35b_caselaw``），仅用于模糊检索。

    与规则库（成文法）的边界：判例只「降权/猜选项」，永不静默抑制；晋级为规则
    须经离线归纳 Agent 提议 + harness 验证 + 人批准（``import35b_suppressions``
    / ``repair_rules`` 才是执行层，本表不参与编译热路径的确定性判断）。
    """

    __tablename__ = "import35b_caselaw"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    # misjudgment（误判库）| solution（方案库）
    kind: Mapped[str] = mapped_column(String(16), index=True, default="")
    suspect_type: Mapped[str] = mapped_column(String(64), index=True, default="")
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    # 指纹继承自 finding：跨页/跨 job 归并同类判例
    fingerprint: Mapped[str] = mapped_column(String(32), index=True, default="")
    # 判例的可检索文本（suspect_type + evidence 摘要），也是 embedding 输入
    case_text: Mapped[str] = mapped_column(Text, default="")
    # 人的裁决快照：{action, scope, note, old, new, candidate_picked}
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    # 溯源：来自哪个 review item / job / page
    review_item_id: Mapped[str] = mapped_column(String(64), default="")
    job_id: Mapped[str] = mapped_column(String(64), default="")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    # 向量索引状态：pending（未入 Milvus）| indexed | skip（无 embedder 时）
    vector_status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[str] = mapped_column(String(64), default="")


class Import35bFeedback(Base):
    """User-reported issues on imported pages — feeds Cursor Repair Pack workflow."""

    __tablename__ = "import35b_feedback"
    __table_args__ = (Index("ix_import35b_feedback_job", "job_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    textbook_key: Mapped[str] = mapped_column(String(256), index=True, default="")
    label: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    block_id: Mapped[str] = mapped_column(String(128), default="")
    screenshot_key: Mapped[str] = mapped_column(String(512), default="")
    input_page_key: Mapped[str] = mapped_column(String(512), default="")
    t1_snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    debug_prefix: Mapped[str] = mapped_column(String(512), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    # open | ack | fixed | wontfix
    status: Mapped[str] = mapped_column(String(32), index=True, default="open")
    pack_path: Mapped[str] = mapped_column(String(512), default="")
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusImportFeedback(Base):
    """User-reported issues on MinerU/corpus import pages — Cursor Pack workflow.

    Fully isolated from ``import35b_feedback`` (separate table + MinIO prefix).
    """

    __tablename__ = "corpus_import_feedback"
    __table_args__ = (Index("ix_corpus_import_feedback_job", "job_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(512), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    block_id: Mapped[str] = mapped_column(String(128), default="")
    screenshot_key: Mapped[str] = mapped_column(String(512), default="")
    input_page_key: Mapped[str] = mapped_column(String(512), default="")
    t1_snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    debug_prefix: Mapped[str] = mapped_column(String(512), default="")
    # open | ack | fixed | wontfix
    status: Mapped[str] = mapped_column(String(32), index=True, default="open")
    pack_path: Mapped[str] = mapped_column(String(512), default="")
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusBookFingerprint(Base):
    """Stable book identity for MinerU line — survives delete/re-import.

    Matching uses content features (page count, TOC titles, first-page text),
    not display title (which users often change).
    """

    __tablename__ = "corpus_book_fingerprints"
    __table_args__ = (Index("ix_corpus_book_fp_hash", "fingerprint_hash"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    toc_hash: Mapped[str] = mapped_column(String(64), default="")
    first_page_hash: Mapped[str] = mapped_column(String(64), default="")
    pdf_bytes_size: Mapped[int] = mapped_column(Integer, default=0)
    label_hint: Mapped[str] = mapped_column(String(512), default="")
    features_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusCorrection(Base):
    """Book-scoped IR patch keyed by fingerprint (from accepted Improvement Points).

    On a later import of the same book (matched by fingerprint), these patches
    are replayed onto IR so MinerU mistakes need not be fixed again.
    """

    __tablename__ = "corpus_corrections"
    __table_args__ = (
        Index("ix_corpus_corr_fp_page", "fingerprint_hash", "page_no"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    fingerprint_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    fingerprint_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    # page | block
    scope: Mapped[str] = mapped_column(String(16), default="page")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    block_id: Mapped[str] = mapped_column(String(128), default="")
    user_note: Mapped[str] = mapped_column(Text, default="")
    before_json: Mapped[str] = mapped_column(Text, default="[]")
    after_json: Mapped[str] = mapped_column(Text, default="[]")
    refine_result_json: Mapped[str] = mapped_column(Text, default="{}")
    source_job_id: Mapped[str] = mapped_column(String(64), default="")
    ip_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    procedure_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusImprovementPoint(Base):
    """First-class improvement point: converged accepted fix with procedure."""

    __tablename__ = "corpus_improvement_points"
    __table_args__ = (
        Index("ix_corpus_ip_fp_status", "fingerprint_hash", "status"),
        Index("ix_corpus_ip_job", "source_job_id"),
        Index("ix_corpus_ip_locus", "locus_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    source_job_id: Mapped[str] = mapped_column(String(64), default="")
    source_session_id: Mapped[str] = mapped_column(String(64), default="")
    locus_key: Mapped[str] = mapped_column(String(256), default="")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(64), default="")
    authority: Mapped[str] = mapped_column(String(32), default="edition")
    status: Mapped[str] = mapped_column(String(32), index=True, default="observed")
    claim_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    procedure_json: Mapped[str] = mapped_column(Text, default="{}")
    concrete_json: Mapped[str] = mapped_column(Text, default="{}")
    pattern_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    pattern_status: Mapped[str] = mapped_column(String(32), default="none")
    superseded_by: Mapped[str] = mapped_column(String(64), default="")
    merged_into_id: Mapped[str] = mapped_column(String(64), default="")
    # 来源闭集：ai_auto | user_ask（见 improvement_points.IP_ORIGINS）
    origin: Mapped[str] = mapped_column(String(32), default="", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(64), default="")
    # 多主题 / 跨页待拆标记（内容质量，不是生命周期状态）
    needs_split: Mapped[int] = mapped_column(Integer, default=0)
    # 预抽象整理状态：needs_triage | needs_split | needs_aggregate | ready
    prep_status: Mapped[str] = mapped_column(String(32), default="needs_triage")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusImprovementLink(Base):
    """Directed relation between improvement points."""

    __tablename__ = "corpus_improvement_links"
    __table_args__ = (Index("ix_corpus_ip_link_from", "from_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    from_id: Mapped[str] = mapped_column(String(64), default="")
    to_id: Mapped[str] = mapped_column(String(64), default="")
    rel: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusImprovementPattern(Base):
    """Reusable abstracted pattern (mode track) across books/pages."""

    __tablename__ = "corpus_improvement_patterns"
    __table_args__ = (Index("ix_corpus_ip_pat_cat", "category", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    category: Mapped[str] = mapped_column(String(64), default="")
    abstract_text: Mapped[str] = mapped_column(Text, default="")
    procedure_template_json: Mapped[str] = mapped_column(Text, default="{}")
    # 粗分层，保留只读兼容；真正决定写哪个生效面的是 landing_target。
    target_layer: Mapped[str] = mapped_column(String(8), default="L1")
    # 闭集，取值见 app.services.improvement_points.LANDING_TARGETS。空 = 尚未定路由。
    landing_target: Mapped[str] = mapped_column(String(32), default="")
    landing_note: Mapped[str] = mapped_column(Text, default="")
    edition_family_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    landed_ref: Mapped[str] = mapped_column(String(512), default="")
    # ── 映射面：让这条模式能在新书里被认出来 ──────────────────────────────
    # 症状名（闭集，与 configs/checks/*.yaml 的 suspect_type 同名）。跨书对齐的锚点。
    suspect_type: Mapped[str] = mapped_column(String(64), default="")
    # 指向哪份检测器声明；空 = 这条模式还查不出来，只能靠人发现。
    detector_ref: Mapped[str] = mapped_column(String(256), default="")
    # rule = 命中即可确定性修复；playbook = 只能查出来，按手册修。
    remediation_kind: Mapped[str] = mapped_column(String(16), default="")
    playbook_ref: Mapped[str] = mapped_column(Text, default="")
    # 成员的症状指纹集合，用于「同症状同证据」的精确复用。
    fingerprints_json: Mapped[str] = mapped_column(Text, default="[]")
    # 检测器质量：命中数、真阳、误报。误报率过高自动降级。
    hits: Mapped[int] = mapped_column(Integer, default=0)
    true_positive: Mapped[int] = mapped_column(Integer, default=0)
    false_positive: Mapped[int] = mapped_column(Integer, default=0)
    # 跨了几本书。晋级门的核心指标：只在一本书里出现的不算规律。
    book_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusSymptomFalsePositive(Base):
    """检测器报了、但人裁决「这不是问题」。

    只降权不屏蔽：同指纹再出现时把严重度降一档并标注，但仍然显示。真正的屏蔽
    是人的权力，要靠改检测器 YAML 收紧判据来完成，不能由统计悄悄决定。
    """

    __tablename__ = "corpus_symptom_false_positives"
    __table_args__ = (
        Index("ix_corpus_symptom_fp_fingerprint", "fingerprint"),
        Index("ix_corpus_symptom_fp_suspect", "suspect_type"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    suspect_type: Mapped[str] = mapped_column(String(64), default="")
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    pattern_id: Mapped[str] = mapped_column(String(64), default="")
    job_id: Mapped[str] = mapped_column(String(64), default="")
    fingerprint_hash: Mapped[str] = mapped_column(String(64), default="")
    page_no: Mapped[int] = mapped_column(Integer, default=0)
    block_id: Mapped[str] = mapped_column(String(64), default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")


class CorpusBookEditionBind(Base):
    """Bind book fingerprint to a book-specific Edition (L2)."""

    __tablename__ = "corpus_book_edition_bind"

    fingerprint_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    edition_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    source_job_id: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class Edition(Base):
    """Catalog row for a textbook Edition Bundle."""

    __tablename__ = "editions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    base_id: Mapped[str] = mapped_column(String(64), default="_base")
    subject: Mapped[str] = mapped_column(String(32), default="math")
    grade_band: Mapped[str] = mapped_column(String(16), default="hs")
    description: Mapped[str] = mapped_column(Text, default="")
    published_version: Mapped[str] = mapped_column(String(32), default="seed")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class EditionVersion(Base):
    """Immutable published snapshot of an Edition Bundle."""

    __tablename__ = "edition_versions"
    __table_args__ = (Index("ix_edition_versions_eid", "edition_id", "version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    edition_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    version: Mapped[str] = mapped_column(String(32), default="")
    bundle_json: Mapped[str] = mapped_column(Text, default="{}")
    note: Mapped[str] = mapped_column(Text, default="")
    published_by: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")


class EditionDraft(Base):
    """Mutable draft bundle edited in the ops console before publish."""

    __tablename__ = "edition_drafts"

    edition_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bundle_json: Mapped[str] = mapped_column(Text, default="{}")
    base_version: Mapped[str] = mapped_column(String(32), default="seed")
    updated_by: Mapped[str] = mapped_column(String(128), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


class EditionCorrectionQueue(Base):
    """Human-gated policy candidates promoted from page feedback."""

    __tablename__ = "edition_correction_queue"
    __table_args__ = (Index("ix_edition_corr_status", "edition_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    edition_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    source: Mapped[str] = mapped_column(String(32), default="feedback")  # feedback|calibration
    source_ref: Mapped[str] = mapped_column(String(128), default="")  # feedback_id etc.
    candidate_json: Mapped[str] = mapped_column(Text, default="{}")  # PolicyEntry-shaped
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|approved|rejected
    reviewer: Mapped[str] = mapped_column(String(128), default="")
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")


# ---------------------------------------------------------------------------
# Knowledge-base line stores everything on disk under
# data/agent-home/textbooks/<book_id>/: meta.json, toc.json, pages/, mid/.
# There is deliberately no ORM model here — see doc/kb-platform-topology.md.
# ---------------------------------------------------------------------------
class TextbookEvalRequest(Base):
    __tablename__ = "textbook_eval_requests"

    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    homework_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    student_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[str] = mapped_column(String(64), default="")
