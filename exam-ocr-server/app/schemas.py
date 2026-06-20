from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

PagePartKind = Literal["header", "section", "question"]


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "c2-gateway"
    backend: str = "vllm-vision"
    vllm_base_url: str
    vllm_model: str
    vllm_reachable: bool = False
    redis_ok: bool = False
    minio_ok: bool = False


class PagePart(BaseModel):
    kind: PagePartKind = "question"
    id: str | None = None
    print_text: str = Field(
        default="",
        validation_alias=AliasChoices("print", "print_text", "stem"),
        serialization_alias="print",
    )
    handwriting: str = Field(
        default="",
        validation_alias=AliasChoices("handwriting", "handwriting_text", "answer", "answer_text"),
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

    @field_validator("kind", mode="before")
    @classmethod
    def kind_to_str(cls, v: Any) -> str:
        s = str(v or "question").lower().strip()
        if s in ("header", "title", "page_header", "页眉", "标题"):
            return "header"
        if s in ("section", "section_title", "大题", "题型"):
            return "section"
        return "question"


class PageAnalyzeResult(BaseModel):
    task: str = "page_analyze"
    parts: list[PagePart] = Field(default_factory=list)
    raw: dict[str, Any] | None = None


# --- Companion pipeline (M0: gateway orchestration, demo A+) ---

SchedulerAction = Literal["init", "merge", "skip", "new_page"]
SessionStatus = Literal["active", "ended"]
MasteryLevel = Literal["unknown", "learning", "mastered"]
SessionEventType = Literal[
    "SESSION_START",
    "FRAME_ADDED",
    "OCR_START",
    "OCR_DONE",
    "OCR_SKIPPED",
    "OCR_REFINED",
    "MERGED",
    "SKIPPED",
    "NEW_PAGE",
    "PAGE_FINALIZED",
    "SESSION_END",
]


class MergeDiff(BaseModel):
    new_question_ids: list[str] = Field(default_factory=list)
    handwriting_growth: dict[str, int] = Field(default_factory=dict)


class QuestionTimingEstimate(BaseModel):
    """Per-question dwell time inferred from progressive capture frames."""

    page_id: str
    question_id: str
    first_answer_at: str
    duration_ms: int = 0
    duration_source: Literal["next_answer", "page_end"] = "next_answer"
    answer_order: int = 1


class SessionEvent(BaseModel):
    ts: str
    type: SessionEventType
    payload: dict[str, Any] = Field(default_factory=dict)


class FrameRecord(BaseModel):
    id: str
    seq: int
    page_id: str
    captured_at: str
    scheduler_action: SchedulerAction
    ocr_parts: list[PagePart] = Field(default_factory=list)
    merge_diff: MergeDiff | None = None
    same_page_score: float | None = None
    duration_ms: int = 0
    ocr_ms: int = 0
    refine_ms: int = 0
    total_ms: int = 0
    skip_reason: str | None = None


class CompanionSession(BaseModel):
    id: str
    status: SessionStatus = "active"
    started_at: str
    ended_at: str | None = None
    current_page_id: str
    merged_parts: list[PagePart] = Field(default_factory=list)
    frames: list[FrameRecord] = Field(default_factory=list)
    events: list[SessionEvent] = Field(default_factory=list)
    question_timings: list[QuestionTimingEstimate] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class HomeworkEntry(BaseModel):
    id: str
    session_id: str
    page_id: str
    finalized_at: str
    source_frame_ids: list[str] = Field(default_factory=list)
    parts: list[PagePart] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class FrameBatchItem(BaseModel):
    frame_id: str
    scheduler_action: SchedulerAction
    merge_diff: MergeDiff | None = None
    same_page_score: float | None = None
    skip_reason: str | None = None
    ocr_ms: int = 0
    refine_ms: int = 0
    total_ms: int = 0
    duration_ms: int = 0


class AddFrameResponse(BaseModel):
    frame_id: str
    scheduler_action: SchedulerAction
    merge_diff: MergeDiff | None = None
    same_page_score: float | None = None
    skip_reason: str | None = None
    ocr_parts: list[PagePart] = Field(default_factory=list)
    merged_parts: list[PagePart] = Field(default_factory=list)
    duration_ms: int = 0
    ocr_ms: int = 0
    refine_ms: int = 0
    total_ms: int = 0
    homework_finalized: HomeworkEntry | None = None
    session: CompanionSession


class AddFramesBatchResponse(BaseModel):
    frames: list[FrameBatchItem] = Field(default_factory=list)
    batch_total_ms: int = 0
    homework_finalized: list[HomeworkEntry] = Field(default_factory=list)
    session: CompanionSession


class MasteryRecord(BaseModel):
    kp_id: str
    label: str
    mastery: MasteryLevel = "unknown"
    updated_at: str
    source_homework_id: str | None = None
    source_session_id: str | None = None
    question_id: str | None = None
    print_text: str = ""
    handwriting: str = ""
    inference_note: str = ""
    demo: bool = True


class CreateSessionRequest(BaseModel):
    subject: str | None = None
    note: str | None = None


class CreateSessionResponse(BaseModel):
    session: CompanionSession


class FinalizePageResponse(BaseModel):
    homework: HomeworkEntry | None = None
    session: CompanionSession


class EndSessionResponse(BaseModel):
    homework: HomeworkEntry | None = None
    session: CompanionSession


class PatchMasteryRequest(BaseModel):
    mastery: MasteryLevel


class HomeworkListResponse(BaseModel):
    entries: list[HomeworkEntry] = Field(default_factory=list)


class MasteryListResponse(BaseModel):
    records: list[MasteryRecord] = Field(default_factory=list)
    demo: bool = True


class SessionExportResponse(BaseModel):
    session: CompanionSession
    homework: list[HomeworkEntry] = Field(default_factory=list)
    mastery: list[MasteryRecord] = Field(default_factory=list)
