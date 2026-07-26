from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

MasteryLevel = Literal["unknown", "learning", "mastered"]
MemoryEventType = Literal[
    "HANDWRITING_GROWTH",
    "PAGE_FINALIZED",
    "QUESTION_TAGGED",
    "QUESTION_DWELL",
    "QUESTION_EVALUATED",
    "MARKED_WRONG",
    "MASTERY_OVERRIDE",
    "MASTERY_REMOVED",
    "OBSERVER_NOTE",
    "EVAL_AGENT_NOTE",
    "DIAGNOSIS_NOTE",
    "PLAN_NOTE",
]

WrongSourceType = Literal["eval", "manual", "pending"] | None
EvalStatusType = Literal["completed", "failed", "pending"] | None


class LabelI18n(BaseModel):
    zh: str = ""
    en: str = ""


MathSegmentKind = Literal["text", "math"]


class MathSegment(BaseModel):
    kind: MathSegmentKind
    raw: str
    latex: str | None = None
    valid: bool = False


class RichText(BaseModel):
    raw: str = ""
    segments: list[MathSegment] = Field(default_factory=list)


class PagePartIn(BaseModel):
    kind: str = "question"
    id: str | None = None
    print_text: str = Field(
        default="",
        validation_alias=AliasChoices("print", "print_text", "stem"),
    )
    handwriting: str = Field(
        default="",
        validation_alias=AliasChoices("handwriting", "handwriting_text", "answer"),
    )
    bbox_print: list[float] = Field(default_factory=list)
    bbox_handwriting: list[float] = Field(default_factory=list)

    model_config = {"populate_by_name": True}

    @field_validator("id", mode="before")
    @classmethod
    def id_to_str(cls, v: Any) -> str | None:
        if v is None or v == "":
            return None
        return str(v)


class KpTag(BaseModel):
    kp_id: str
    score: float
    source: str = ""
    label: str = ""
    label_i18n: LabelI18n | None = None


class RagEvidence(BaseModel):
    kp_id: str
    score: float
    source: str
    text: str = ""


class StudentOut(BaseModel):
    id: str
    label: str
    created_at: str


class QuestionOut(BaseModel):
    id: str
    homework_id: str
    ordinal: str
    print_text: str
    handwriting: str
    print_rich: RichText | None = None
    handwriting_rich: RichText | None = None
    bbox_print: list[float] = Field(default_factory=list)
    bbox_handwriting: list[float] = Field(default_factory=list)
    is_wrong: bool | None = None
    dwell_ms: int | None = None
    eval_confidence: float | None = None
    eval_json: dict[str, Any] = Field(default_factory=dict)
    agent_is_wrong: bool | None = None
    wrong_source: WrongSourceType = None
    eval_status: EvalStatusType = None
    reference_answer: str | None = None
    match_notes: str | None = None
    kp_tags: list[KpTag] = Field(default_factory=list)
    rag_evidence: list[RagEvidence] = Field(default_factory=list)
    created_at: str = ""


class HomeworkOut(BaseModel):
    id: str
    student_id: str
    session_id: str
    page_id: str
    finalized_at: str
    source_frame_ids: list[str] = Field(default_factory=list)
    parts: list[PagePartIn] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    questions: list[QuestionOut] = Field(default_factory=list)


class MemoryEventIn(BaseModel):
    session_id: str = ""
    ts: str
    type: MemoryEventType
    question_id: str | None = None
    kp_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class IngestHomeworkRequest(BaseModel):
    id: str
    session_id: str
    page_id: str
    finalized_at: str
    source_frame_ids: list[str] = Field(default_factory=list)
    parts: list[PagePartIn] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class IngestEventsRequest(BaseModel):
    events: list[MemoryEventIn] = Field(default_factory=list)


class MasteryEvidence(BaseModel):
    homework_id: str
    question_id: str
    ts: str = ""
    note: str = ""


class MasteryViewOut(BaseModel):
    student_id: str
    kp_id: str
    label: str
    label_i18n: LabelI18n | None = None
    mastery: MasteryLevel
    confidence: float = 0.0
    weak: bool = False
    fluency: float = 0.0
    fluency_ratio: float = 0.0
    wrong_rate: float = 0.0
    evidence: list[MasteryEvidence] = Field(default_factory=list)
    updated_at: str
    demo: bool = False


class PatchQuestionRequest(BaseModel):
    is_wrong: bool | None = None
    kp_tags: list[KpTag] | None = None
    print_text: str | None = None
    handwriting: str | None = None
    restore_agent: bool = False


class PatchHomeworkRequest(BaseModel):
    meta: dict[str, Any] | None = None
    finalized_at: str | None = None


class PostMasteryRequest(BaseModel):
    kp_id: str
    mastery: MasteryLevel = "unknown"
    label: str = ""


class PatchMasteryRequest(BaseModel):
    mastery: MasteryLevel
    weak: bool | None = None
    confidence: float | None = None


class WrongBookItemOut(BaseModel):
    question_id: str
    homework_id: str
    ordinal: str
    print_text: str = ""
    handwriting: str = ""
    print_rich: RichText | None = None
    handwriting_rich: RichText | None = None
    kp_tags: list[KpTag] = Field(default_factory=list)
    finalized_at: str = ""
    is_wrong: bool | None = None
    wrong_source: WrongSourceType = None
    agent_is_wrong: bool | None = None
    eval_status: EvalStatusType = None
    eval_confidence: float | None = None
    reference_answer: str | None = None
    match_notes: str | None = None
    eval_json: dict[str, Any] = Field(default_factory=dict)
    candidate_reason: str | None = None


class WrongBookOut(BaseModel):
    items: list[WrongBookItemOut] = Field(default_factory=list)
    candidates: list[WrongBookItemOut] = Field(default_factory=list)
    count: int = 0
    candidate_count: int = 0


class QuestionEvalTraceOut(BaseModel):
    question_id: str
    ordinal: str
    is_wrong: bool | None = None
    reference_answer: str | None = None
    eval_confidence: float | None = None
    eval_trace: dict[str, Any] = Field(default_factory=dict)


class HomeworkEvalTracesOut(BaseModel):
    homework_id: str
    student_id: str
    trace_count: int = 0
    question_count: int = 0
    avg_ms: int = 0
    log_file: str = ""
    questions: list[QuestionEvalTraceOut] = Field(default_factory=list)


class SyllabusKpOut(BaseModel):
    kp_id: str
    label: str
    label_i18n: LabelI18n
    description: str = ""
    level: int = 1
    parent_id: str | None = None


class RagSearchHit(BaseModel):
    kp_id: str
    score: float
    source: str
    text: str
    label: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "learning-buddy-api"
    db_ok: bool = True
    milvus_ok: bool = False
    qdrant_ok: bool = False
    nemo_embed_ok: bool = False
    nemo_rerank_ok: bool = False
    redis_ok: bool = False
    chroma_ok: bool = False  # deprecated alias
    vector_backend: str = "milvus"
    syllabus_count: int = 0
    question_bank_count: int = 0
    embed_model: str = ""
    max_students: int = 5
