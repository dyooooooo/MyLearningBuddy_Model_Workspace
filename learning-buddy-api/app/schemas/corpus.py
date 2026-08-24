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
        description='MinerU Cloud 页范围，如 "1-178"；与 page_limit 互斥，优先本字段。全书导入通常不传',
    )
    # Hard-break: explicit Edition bundle required (no implicit 沪教 default).
    edition_id: str = Field(..., min_length=1, description="Edition bundle id, e.g. hujiao_math_hs_v1")
    inherit_from_job_id: str = Field(
        default="",
        description="同一本书重导：解析完成后把该旧任务改好的页整页继承过来（空=重新生成）",
    )


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


class IpAttemptIngestRequest(BaseModel):
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    job_id: str = ""
    session_id: str = ""
    fingerprint_hash: str = ""


class IpConfirmRequest(BaseModel):
    job_id: str = ""
    session_id: str = ""
    fingerprint_hash: str = ""
    page_no: int = 0
    category: str = ""
    summary: str = ""
    authority: str = "edition"
    kind: str = ""
    block_ids: list[str] = Field(default_factory=list)
    slot: str = ""
    procedure: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    concrete: dict[str, Any] | None = None
    pattern_abstract: str = ""
    pattern_status: str = "none"
    edition_family_id: str = ""
    write_correction: bool = True
    # Closed set: ai_auto | user_ask (legacy codes mapped server-side)
    origin: str = ""
    # 显式待拆；None = 按摘要启发式
    needs_split: bool | None = None


class IpConvergeRequest(BaseModel):
    job_id: str = ""
    session_id: str = ""
    fingerprint_hash: str = ""


class IpResolveConflictRequest(BaseModel):
    winner: bool = True


class IpSplitProposeRequest(BaseModel):
    """拆分提案：可选带 step_indices/after_indices 的子条规格（Agent 组装）。"""

    children: list[dict] = Field(default_factory=list)


class IpSplitApplyRequest(BaseModel):
    """拆分确认：完备子条列表（须含 procedure_slice 或 step_indices）。"""

    children: list[dict] = Field(default_factory=list)


class IpPrepConflictResolveRequest(BaseModel):
    """整理态矛盾/聚合裁决（与 lifecycle resolve-conflict 不同）。"""

    keep_ids: list[str] = Field(default_factory=list)
    delete_ids: list[str] = Field(default_factory=list)


class IpTriageApplyRequest(BaseModel):
    """Cursor Agent 整理结果落库（无模型）。"""

    fingerprint_hash: str = ""
    ready_ids: list[str] = Field(default_factory=list)
    split_ids: list[str] = Field(default_factory=list)
    aggregate_groups: list[dict] = Field(default_factory=list)


class IpPatternRequest(BaseModel):
    pattern_status: str = "none"
    abstract_text: str = ""
    # 空 = 不改动模式已有的分层；定路由请用 POST /patterns/{id}/route。
    target_layer: str = ""
    edition_family_id: str = ""
    # 症状名（闭集，全大写下划线）。给了就按它归并，这是跨书对齐的锚点；
    # 不给才退回按摘要文本哈希归并。
    suspect_type: str = ""


class PatternRouteRequest(BaseModel):
    """定路由：这条规律最终写到哪个生效面（取值见 LANDING_TARGETS 闭集）。"""

    landing_target: str
    note: str = ""


class PatternLandRequest(BaseModel):
    """落地记账：写盘由 buddy-copilot 走批准闸门完成，这里只回填 landed_ref。"""

    landed_ref: str
    note: str = ""


class PatternMergeRequest(BaseModel):
    merge_ids: list[str] = Field(default_factory=list)
    note: str = ""


class PatternSplitRequest(BaseModel):
    """把混在一个桶里的另一条规律拆出去。"""

    member_ip_ids: list[str] = Field(default_factory=list)
    abstract_text: str = ""
    category: str = ""
    note: str = ""


class PatternBindDetectorRequest(BaseModel):
    """把模式和它的症状名、检测器声明、处置档位绑起来。"""

    suspect_type: str
    detector_ref: str = ""
    remediation_kind: str = ""  # rule | playbook
    playbook_ref: str = ""
    landing_target: str = ""


class CheckDryRunRequest(BaseModel):
    """在真实导入页上试跑检测器。``yaml_text`` 给未落盘的草稿，优先于 suspect_type。"""

    job_id: str
    suspect_type: str = ""
    yaml_text: str = ""
    pages: list[int] = Field(default_factory=list)
    limit: int = 40


class CheckExamplesRequest(BaseModel):
    suspect_type: str = ""
    yaml_text: str = ""


class CheckFalsePositiveRequest(BaseModel):
    """人裁决「这不是问题」。"""

    suspect_type: str
    fingerprint: str = ""
    job_id: str = ""
    page_no: int = 0
    block_id: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class BookEditionBindRequest(BaseModel):
    edition_id: str
    source_job_id: str = ""
    note: str = ""


class MarkdownPatch(BaseModel):
    markdown: str


class RebuildIrRequest(BaseModel):
    pages: list[int] | None = None


class AdoptFingerprintRequest(BaseModel):
    """认亲: pin an import job to a prior book identity so L3 replay applies."""

    fingerprint_hash: str
    note: str = ""
    replay: bool = False


class InheritPagesRequest(BaseModel):
    """Carry finished pages from a previous import job into this one."""

    from_job_id: str
    pages: list[int] | None = None
    only_improved: bool = True
    dry_run: bool = True
    max_offset: int = 8
    include_low_confidence: bool = False


class FinalizeBookRequest(BaseModel):
    """定稿: keep this import as the book's only version, delete the rest."""

    dry_run: bool = True
    ack_warnings: bool = False
    note: str = ""


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
