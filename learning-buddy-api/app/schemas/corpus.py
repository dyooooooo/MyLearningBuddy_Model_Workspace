from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SourceChannel = Literal[
    "companion",
    "scout",
    "pdf_import",
    "photo",
    "bilibili",
    "class_screenshot",
    "manual",
]

NodeStatus = Literal["draft", "review", "published", "archived", "rejected"]

ImportJobStatus = Literal[
    "uploaded",
    "converting",
    "parsed",
    "chunking",
    "chunked",
    "indexed",
    "failed",
]


class CorpusNodeOut(BaseModel):
    id: str
    node_type: str
    subject: str
    grade_band: str
    label: str = ""
    summary: str = ""
    full_text: str = ""
    tags: list[str] = Field(default_factory=list)
    links: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    status: str = "draft"
    updated_at: str = ""
    backlink_count: int = 0
    vector_indexed: bool = False


class NavCrumb(BaseModel):
    id: str
    label: str


class NavContextOut(BaseModel):
    """Single-round-trip breadcrumb + optional first page chunk for Studio."""

    node: CorpusNodeOut
    breadcrumb: list[NavCrumb] = Field(default_factory=list)
    first_page_chunk_id: str | None = None


class CorpusNodeCreate(BaseModel):
    id: str
    node_type: str = "chunk"
    subject: str = "math"
    grade_band: str = "hs"
    label: str = ""
    summary: str = ""
    full_text: str = ""
    tags: list[str] = Field(default_factory=list)
    links: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    status: NodeStatus = "draft"


class CorpusNodePatch(BaseModel):
    label: str | None = None
    summary: str | None = None
    full_text: str | None = None
    tags: list[str] | None = None
    links: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    status: NodeStatus | None = None


class CorpusEdgeCreate(BaseModel):
    to_id: str
    rel_type: str
    weight: float = 1.0


class ImportPdfRequest(BaseModel):
    minio_key: str
    subject: str = "math"
    grade_band: str = "hs"
    label: str = ""
    textbook_id: str = ""
    source_channel: SourceChannel = "pdf_import"
    parse_engine: Literal["mineru_cloud"] = "mineru_cloud"
    page_limit: int | None = Field(default=None, ge=1, description="仅导入前 N 页；None 表示全部")
    page_ranges: str | None = Field(
        default=None,
        description='MinerU Cloud 页范围，如 "1-20" / "21-40"；与 page_limit 互斥，优先本字段',
    )
    # Hard-break: explicit Edition bundle required (no implicit 沪教 default).
    edition_id: str = Field(..., min_length=1, description="Edition bundle id, e.g. hujiao_math_hs_v1")


class ImportExtendRequest(BaseModel):
    """同 job 追加 MinerU 分段识别（AI 分段导入）。"""

    page_ranges: str = Field(..., min_length=1, description='如 "21-40"')


class ImportJobOut(BaseModel):
    id: str
    status: str
    source_channel: str
    minio_key: str = ""
    markdown: str = ""
    textbook_node_id: str = ""
    subject: str
    grade_band: str
    label: str = ""
    error: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


class MarkdownPatch(BaseModel):
    markdown: str


class RebuildIrRequest(BaseModel):
    pages: list[int] | None = None


class SearchPreviewRequest(BaseModel):
    query: str
    subject: str = "math"
    grade_band: str = "hs"
    collection: Literal["production", "staging"] = "production"
    top_k: int = 5


class ReindexRequest(BaseModel):
    force: bool = False
    node_ids: list[str] = Field(default_factory=list)


class ScoutRequest(BaseModel):
    query: str = ""
    url: str = ""
    subject: str = "math"
    grade_band: str = "hs"
    resource_kinds: list[str] = Field(default_factory=lambda: ["article", "video"])


class InboxItemOut(BaseModel):
    id: str
    scout_run_id: str = ""
    status: str
    source_url: str = ""
    source_platform: str = ""
    resource_kind: str = ""
    title: str = ""
    snippet: str = ""
    proposed_node_ids: list[str] = Field(default_factory=list)
    agent_confidence: float = 0.0
    agent_notes: dict[str, Any] = Field(default_factory=dict)
    subject: str = "math"
    grade_band: str = "hs"
    created_at: str = ""


class ScoutRunOut(BaseModel):
    id: str
    query: str = ""
    url: str = ""
    subject: str
    grade_band: str
    status: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    inbox_count: int = 0
    started_at: str = ""
    finished_at: str = ""
    error: str = ""


class CorpusStatsOut(BaseModel):
    nodes_by_status: dict[str, int] = Field(default_factory=dict)
    nodes_by_type: dict[str, int] = Field(default_factory=dict)
    milvus_production_count: int = 0
    milvus_staging_count: int = 0
    published_unindexed: int = 0
    embed_model: str = ""
    manifest_stale: bool = False
    milvus_schema_ok: bool = True


class TreeNodeOut(BaseModel):
    id: str
    label: str
    node_type: str
    status: str = ""
    child_count: int = 0
    has_children: bool = False
    meta_hint: str = ""
    # Subset of CorpusNode.meta for sidebar actions (e.g. import page → preview).
    meta: dict[str, Any] = Field(default_factory=dict)


class ReaderPageOut(BaseModel):
    """One PDF page in the continuous textbook reader."""

    id: str
    label: str
    source_page: int
    section_id: str = ""
    status: str = ""
    import_job_id: str = ""


class LinkEntryOut(BaseModel):
    node_id: str
    label: str
    rel_type: str
    node_type: str = ""
    group: str = "other"


class NodeLinksOut(BaseModel):
    outlinks: list[LinkEntryOut] = Field(default_factory=list)
    inlinks: list[LinkEntryOut] = Field(default_factory=list)
    structural: list[LinkEntryOut] = Field(default_factory=list)
    kp: list[LinkEntryOut] = Field(default_factory=list)
    tags: list[LinkEntryOut] = Field(default_factory=list)
    other: list[LinkEntryOut] = Field(default_factory=list)
    hidden_count: int = 0


class WorkQueueOut(BaseModel):
    draft_chunks: list[CorpusNodeOut] = Field(default_factory=list)
    review_nodes: list[CorpusNodeOut] = Field(default_factory=list)
    unindexed_published: list[CorpusNodeOut] = Field(default_factory=list)
    failed_imports: list[ImportJobOut] = Field(default_factory=list)
    structure_review_imports: list[ImportJobOut] = Field(
        default_factory=list,
        description="PDF textbook imports needing TOC deepen review",
    )
    inbox_ready: list[InboxItemOut] = Field(default_factory=list)


class ReindexAsyncOut(BaseModel):
    job_id: str
    status: str = "queued"


class ReindexJobStatusOut(BaseModel):
    job_id: str
    status: str
    done: int = 0
    total: int = 0
    errors: list[str] = Field(default_factory=list)


class BulkPublishRequest(BaseModel):
    node_ids: list[str] = Field(default_factory=list)


class SetStatusRequest(BaseModel):
    status: NodeStatus


class PublishSubtreeOut(BaseModel):
    root_id: str
    published_count: int
    node_ids: list[str] = Field(default_factory=list)


class GraphOut(BaseModel):
    nodes: list[CorpusNodeOut] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class PromoteQuestionRequest(BaseModel):
    anonymize: bool = True
    target_node_type: str = "chunk"
