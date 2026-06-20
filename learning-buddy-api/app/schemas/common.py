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
    "OBSERVER_NOTE",
    "EVAL_AGENT_NOTE",
    "DIAGNOSIS_NOTE",
    "PLAN_NOTE",
]


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
    bbox_print: list[float] = Field(default_factory=list)
    bbox_handwriting: list[float] = Field(default_factory=list)
    is_wrong: bool | None = None
    dwell_ms: int | None = None
    eval_confidence: float | None = None
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


class PatchMasteryRequest(BaseModel):
    mastery: MasteryLevel


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
