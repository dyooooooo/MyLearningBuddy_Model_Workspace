// Corpus (MinerU line) API client.
//
// Reconstructed 1:1 from the shipped Studio bundle contract. All calls route
// through the gateway corpus proxy `/api/v1/studio/proxy/*` → buddy-api
// `/api/v1/admin/corpus/*`. This client serves ONLY the MinerU line.
//
// The 35B line has a SEPARATE client (./import35b.ts) hitting `/proxy35b/*`.
// Do NOT add 35B endpoints here — the two lines share no proxy path.

import type {
  ContentPack,
  CorpusNode,
  CorpusStats,
  GraphData,
  ImportJob,
  NodeLinks,
  PipelineStatus,
  ReindexJobStatus,
  TreeNode,
  WorkQueueData,
} from "./types";
import { studioFetch, type HttpMethod } from "./http";

const PROXY = "/api/v1/studio/proxy";

/** 一条命名症状（症状名 + 位置 + 处置档位 + 上次的成功做法）。 */
export interface SymptomFinding {
  suspect_type: string;
  severity?: string;
  page_no?: number;
  block_id?: string;
  fingerprint?: string;
  evidence?: Record<string, unknown>;
  candidates?: string[];
  pattern_id?: string;
  /** exact = 同指纹命中历史修正；suspect_type = 只对上症状名 */
  match?: string;
  /** rule 能自动修，playbook 只能按手册修 */
  remediation_kind?: string;
  landing_target?: string;
  /** 手册出处指针 */
  playbook_ref?: string;
  /** 手册步骤全文，服务端按指针从检测器 YAML 解析 */
  playbook?: string;
  false_positive_hits?: number;
}

export interface JobSymptoms {
  job_id: string;
  summary: {
    total?: number;
    by_suspect_type?: Record<string, number>;
    known_pages?: number[];
    unknown_pages?: number[];
  };
  /** none / known / unknown / mixed */
  coverage: string;
  known: SymptomFinding[];
  unknown: SymptomFinding[];
  /** 数值兜底的低置信页，与命名症状并列，不混为一谈 */
  low_confidence_pages: number[];
}

/** 一条已生效的检测器声明（configs/checks 里的 YAML 读出来的）。 */
export interface SymptomCheckInfo {
  suspect_type: string;
  title?: string;
  severity?: string;
  enabled?: boolean;
  /** block = 逐块判定；page = 整页判定一次 */
  scope?: string;
  /** 判据表达式本身 */
  when?: string;
  min_hits?: number;
  evidence?: string[];
  /** evidence 里「不给就没法动手」的那几项，改进器只拿这个子集 */
  locate_by?: string[];
  remediation_kind?: string;
  landing_target?: string;
  playbook?: string;
  rule_ref?: string;
  has_examples?: boolean;
  /** 正反例样例文件（相对 learning-buddy-api/ 的路径） */
  examples?: { positive: string[]; negative: string[] };
  /** YAML 文件名 */
  source?: string;
}

/** 正反例回归结果：召回不满 100% 或反例有误报就算没过。 */
export interface CheckExamplesResult {
  ok: boolean;
  suspect_type: string;
  passed: boolean;
  recall: number;
  false_positive_rate: number;
  positive_count: number;
  negative_count: number;
  detail: {
    file: string;
    polarity: "positive" | "negative";
    hit: boolean;
    ok: boolean;
  }[];
  verdict: string;
}

/** 在真实导入页上试跑的结果（不落库、不改文件）。 */
export interface CheckDryRunResult {
  ok: boolean;
  suspect_type: string;
  scanned_pages: number;
  hit_count: number;
  hit_pages: number[];
  truncated: boolean;
  hits: SymptomFinding[];
}

/** 一份没能加载的声明：不在 checks 里，也就根本没在跑。 */
export interface CheckLoadError {
  file: string;
  errors: string[];
}

/** features.yaml 里的派生特征：用原子词组合出的命名特征。 */
export interface DerivedFeatureInfo {
  name: string;
  expr: string;
  /** 引用了块级字段的特征在 scope: page 里用不了 */
  page_safe: boolean;
}

/** 词的来源层级：改它要不要重建镜像，全看这一栏。 */
export type VocabLayer = "L0" | "L1" | "L2" | "L2b" | "derived";

/** 一个词的档案：来源层级 + 中文释义 + 依赖 + 谁在用。 */
export interface VocabWordInfo {
  name: string;
  layer: VocabLayer;
  /** block | page | both */
  scope: string;
  doc: string;
  /** L2 配置词的表达式原文；L0/L1/插件词为空 */
  expr: string;
  depends_on: string[];
  /** 最终会用到它的检测器文件名（含经由别的词的间接引用） */
  used_by: string[];
  /** 直接引用它的配置词与派生特征 */
  used_by_words: string[];
  /** 孤词：没有任何检测器会因为它命中 */
  unused: boolean;
  /** 定义它的文件（挂载层才有） */
  source: string;
}

/** lexicons.yaml 里的一份命名词表（中文例外词只能以这种形式存在）。 */
export interface LexiconInfo {
  name: string;
  doc: string;
  size: number;
}

/** 词表体检：孤词、重复、命中量。 */
export interface VocabDiagnostics {
  total: number;
  unused: string[];
  /** 挂载层（L2 / L2b / 派生）的孤词，最该先清的就是这些 */
  unused_hot: string[];
  coverage: Record<string, { total: number; used: number }>;
  duplicate_expr: { expr: string; names: string[] }[];
  same_users: { used_by: string[]; names: string[] }[];
  /** 只有带 session 的调用才有：用到该词的检测器累计命中与误报 */
  hits?: {
    name: string;
    hits: number;
    false_positive: number;
    false_positive_rate: number | null;
  }[];
}

export interface ChecksOverview {
  ok: boolean;
  checks_dir: string;
  count: number;
  load_errors: CheckLoadError[];
  checks: SymptomCheckInfo[];
  vocabulary: {
    page: Record<string, unknown>;
    block: Record<string, unknown>;
    functions: string[];
    derived: DerivedFeatureInfo[];
    /** 名字 → 中文释义，判据里每个词都能查到 */
    docs: Record<string, string>;
    /** 层级代号 → 这一层是什么、改它要不要重建镜像 */
    layers?: Record<string, string>;
    words?: VocabWordInfo[];
    lexicons?: LexiconInfo[];
    diagnostics?: VocabDiagnostics;
  };
}

/** 定稿（finalize）报告：`remove` 里的每一版在执行时会被连数据删除。 */
export interface FinalizeBookReport {
  keep: {
    job_id: string;
    label: string;
    status: string;
    improvement_points: number;
    corrections: number;
    node_count: number;
    already_finalized?: boolean;
  };
  remove: {
    job_id: string;
    label: string;
    status: string;
    /** 判定同一本书的依据：fingerprint / same_file / same_body_pages / similar_body_pages */
    match: string;
    improvement_points: number;
    corrections: number;
    node_count: number;
    feedback_count: number;
  }[];
  remove_count: number;
  editions_to_drop: { edition_id: string; display_name: string }[];
  warnings: string[];
  dry_run?: boolean;
  applied?: boolean;
  totals?: Record<string, number>;
}

async function call(
  method: HttpMethod,
  path: string,
  body?: unknown,
  query?: Record<string, string>,
): Promise<any> {
  return studioFetch(`${PROXY}/${path}`, { method, body, query });
}

export const corpus = {
  // --- corpus tree / nodes ---
  stats: (): Promise<CorpusStats> => call("GET", "stats"),
  tree: (
    root: string,
    parentId = "",
    subject = "math",
    gradeBand = "hs",
  ): Promise<TreeNode[]> =>
    call("GET", "tree", undefined, {
      root,
      parent_id: parentId,
      subject,
      grade_band: gradeBand,
    }),
  getNode: (id: string): Promise<CorpusNode> =>
    call("GET", `nodes/${encodeURIComponent(id)}`),
  /** Breadcrumb + optional first page chunk (single round-trip). */
  navContext: (
    id: string,
  ): Promise<{
    node: CorpusNode;
    breadcrumb: { id: string; label: string }[];
    first_page_chunk_id?: string | null;
  }> => call("GET", `nodes/${encodeURIComponent(id)}/nav-context`),
  readerPages: (
    id: string,
  ): Promise<
    {
      id: string;
      label: string;
      source_page: number;
      section_id?: string;
      status?: string;
      import_job_id?: string;
    }[]
  > => call("GET", `nodes/${encodeURIComponent(id)}/reader-pages`),
  patchNode: (id: string, patch: Partial<CorpusNode>): Promise<CorpusNode> =>
    call("PATCH", `nodes/${encodeURIComponent(id)}`, patch),
  searchNodes: (q: string, limit = "50"): Promise<CorpusNode[]> =>
    call("GET", "nodes/search", undefined, { q, limit }),
  nodeLinks: (id: string, hide?: string): Promise<NodeLinks> =>
    call(
      "GET",
      `nodes/${encodeURIComponent(id)}/links`,
      undefined,
      hide !== undefined ? { hide } : undefined,
    ),
  workqueue: (limit = "20"): Promise<WorkQueueData> =>
    call("GET", "workqueue", undefined, { limit }),
  publish: (id: string): Promise<any> =>
    call("POST", `nodes/${encodeURIComponent(id)}/publish`),
  publishSubtree: (id: string): Promise<any> =>
    call("POST", `nodes/${encodeURIComponent(id)}/publish-subtree`),
  embedStaging: (id: string): Promise<any> =>
    call("POST", `nodes/${encodeURIComponent(id)}/embed-staging`),
  setStatus: (id: string, status: string): Promise<any> =>
    call("POST", `nodes/${encodeURIComponent(id)}/set-status`, { status }),
  addEdge: (id: string, toId: string, relType: string): Promise<any> =>
    call("POST", `nodes/${encodeURIComponent(id)}/edges`, {
      to_id: toId,
      rel_type: relType,
    }),
  deleteNode: (id: string, cascade = false): Promise<any> =>
    call(
      "DELETE",
      `nodes/${encodeURIComponent(id)}`,
      undefined,
      cascade ? { cascade: "true" } : undefined,
    ),
  searchPreview: (q: string, collection = "production"): Promise<any> =>
    call("POST", "search-preview", {
      query: q,
      subject: "math",
      grade_band: "hs",
      collection,
      top_k: 5,
    }),
  submitReview: (id: string): Promise<any> =>
    call("POST", `nodes/${encodeURIComponent(id)}/submit-review`),

  // --- index / reindex ---
  reindex: (): Promise<any> => call("POST", "reindex", { force: false }),
  reindexAsync: (): Promise<{ job_id: string }> =>
    call("POST", "reindex/async", { force: false }),
  reindexJob: (jobId: string): Promise<ReindexJobStatus> =>
    call("GET", `reindex/jobs/${jobId}`),

  // --- graph ---
  graph: (
    center: string,
    includeDraft = true,
    opts?: { root?: string; relTypes?: string },
  ): Promise<GraphData> =>
    call("GET", "graph", undefined, {
      center,
      hops: "2",
      limit: "80",
      include_draft: String(includeDraft),
      ...(opts && opts.root ? { root: opts.root } : {}),
      ...(opts && opts.relTypes ? { rel_types: opts.relTypes } : {}),
    }),

  // --- import (MinerU line) ---
  importJobs: (): Promise<ImportJob[]> => call("GET", "import/jobs"),
  importJob: (id: string): Promise<ImportJob> =>
    call("GET", `import/jobs/${id}`),
  pipelineStatus: (): Promise<PipelineStatus> => call("GET", "pipeline/status"),
  cancelImport: (id: string): Promise<any> =>
    call("POST", `import/jobs/${id}/cancel`),
  deleteImport: (id: string): Promise<any> =>
    call("DELETE", `import/jobs/${id}`),
  importBlocks: (id: string): Promise<any> =>
    call("GET", `import/jobs/${id}/blocks`),
  patchImportBlock: (id: string, blockId: string, patch: unknown): Promise<any> =>
    call("PATCH", `import/jobs/${id}/blocks/${encodeURIComponent(blockId)}`, patch),
  rerenderImportPage: (id: string, pageNo: number): Promise<any> =>
    call("POST", `import/jobs/${id}/rerender-page/${pageNo}`),
  /** 这次导入命中的命名症状：known 有成功路径，unknown 需诊断。 */
  jobSymptoms: (id: string): Promise<JobSymptoms> =>
    call("GET", `jobs/${id}/symptoms`),
  /**
   * 生效中的检测器 + 词表 + 没能加载的声明。
   *
   * `load_errors` 是这份数据里最该先看的一段：写坏的声明不会出现在 `checks` 里，
   * 只看 `checks` 会以为一切正常，实际那条检测器根本没在跑。
   */
  checksOverview: (): Promise<ChecksOverview> => call("GET", "checks"),
  /** 跑这条检测器自带的正反例：召回率 + 误报率，是「准不准」的唯一硬证据。 */
  runCheckExamples: (suspectType: string): Promise<CheckExamplesResult> =>
    call("POST", "checks/run-examples", { suspect_type: suspectType }),
  /** 在某次真实导入的指定页上试跑（不落库、不改文件）。 */
  dryRunCheck: (body: {
    job_id: string;
    suspect_type: string;
    pages?: number[];
    limit?: number;
  }): Promise<CheckDryRunResult> => call("POST", "checks/dry-run", body),
  /** 人判「这不是问题」：只降权不屏蔽，同指纹下次仍会显示。 */
  reportSymptomFalsePositive: (body: {
    suspect_type: string;
    fingerprint: string;
    job_id?: string;
    page_no?: number;
    block_id?: string;
    note?: string;
  }): Promise<{ ok: boolean; id: string; pattern_id: string }> =>
    call("POST", "checks/false-positive", body),
  getImportPageHtml: (
    id: string,
    page: number,
    opts?: { bustCache?: boolean },
  ): Promise<any> =>
    call(
      "GET",
      `import/jobs/${id}/page/${page}/html`,
      undefined,
      opts?.bustCache ? { _ts: String(Date.now()) } : undefined,
    ),
  rebuildImportIr: (id: string, pages?: number[]): Promise<any> =>
    call("POST", `import/jobs/${id}/rebuild-ir`, pages ? { pages } : {}),
  /** Re-run chapter/TOC structure from existing IR (purge stale TOC, keep page_import). */
  structureMerge: (
    id: string,
    body: { purge?: boolean } = { purge: true },
  ): Promise<ImportJob> =>
    call("POST", `import/jobs/${encodeURIComponent(id)}/structure-merge`, body),
  /** 定稿预览：留哪一版、删哪些版、有哪些告警（只读）。 */
  finalizeBookPreview: (id: string): Promise<FinalizeBookReport> =>
    call("GET", `import/jobs/${encodeURIComponent(id)}/finalize-book`),
  /** 定稿执行：本版成为该教材唯一版本，其余同书导入连数据一起删除。 */
  finalizeBook: (
    id: string,
    body: { dry_run?: boolean; ack_warnings?: boolean; note?: string } = {},
  ): Promise<FinalizeBookReport> =>
    call("POST", `import/jobs/${encodeURIComponent(id)}/finalize-book`, body),
  unfinalizeBook: (id: string): Promise<any> =>
    call("POST", `import/jobs/${encodeURIComponent(id)}/unfinalize-book`),
  reprocessPage: (
    id: string,
    pageNo: number,
    body: { mode?: string; user_note?: string; note?: string } = {},
  ): Promise<any> =>
    call("POST", `import/jobs/${id}/pages/${pageNo}/reprocess`, body),
  refineBlock: (
    id: string,
    pageNo: number,
    blockId: string,
    body: { user_note?: string; note?: string } = {},
  ): Promise<any> =>
    call(
      "POST",
      `import/jobs/${id}/pages/${pageNo}/blocks/${encodeURIComponent(blockId)}/refine`,
      body,
    ),
  rerecognizeContext: (
    id: string,
    pageNo: number,
    blockId = "",
  ): Promise<any> =>
    call(
      "GET",
      `import/jobs/${id}/pages/${pageNo}/rerecognize-context`,
      undefined,
      blockId ? { block_id: blockId } : undefined,
    ),
  pageDebugIndex: (id: string, pageNo: number): Promise<any> =>
    call("GET", `import/jobs/${id}/pages/${pageNo}/debug`),
  pageDebugArtifact: (id: string, pageNo: number, artifact: string): Promise<any> =>
    call("GET", `import/jobs/${id}/pages/${pageNo}/debug/${encodeURIComponent(artifact)}`),
  listFeedback: (id: string): Promise<any> =>
    call("GET", `import/jobs/${id}/feedback`),
  createFeedback: async (
    id: string,
    pageNo: number,
    opts: { body: string; tags?: string[]; blockId?: string; screenshot?: Blob | null },
  ): Promise<any> => {
    const fd = new FormData();
    fd.append("body", opts.body);
    if (opts.tags?.length) fd.append("tags", JSON.stringify(opts.tags));
    if (opts.blockId) fd.append("block_id", opts.blockId);
    if (opts.screenshot) fd.append("screenshot", opts.screenshot, "shot.jpg");
    return studioFetch(
      `/api/v1/studio/proxy/import/jobs/${encodeURIComponent(id)}/pages/${pageNo}/feedback`,
      { method: "POST", rawBody: fd },
    );
  },
  buildCursorPack: (feedbackId: string): Promise<any> =>
    call("POST", `import/feedback/${encodeURIComponent(feedbackId)}/cursor-pack`),
  patchMarkdown: (id: string, markdown: string): Promise<any> =>
    call("PATCH", `import/jobs/${id}/markdown`, { markdown }),
  chunkJob: (id: string): Promise<any> => call("POST", `import/jobs/${id}/chunk`),

  // --- inbox / scout / content packs ---
  inbox: (): Promise<any[]> => call("GET", "inbox"),
  approveInbox: (id: string, publish = false): Promise<any> =>
    call("POST", `inbox/${encodeURIComponent(id)}/approve?publish=${publish}`),
  rejectInbox: (id: string): Promise<any> => call("POST", `inbox/${id}/reject`),
  scout: (q: string): Promise<any> =>
    call("POST", "scout", { query: q, subject: "math", grade_band: "hs" }),
  contentPacks: (): Promise<ContentPack[]> =>
    studioFetch<ContentPack[]>("/api/v1/studio/content-packs"),
};

// --- PDF upload / probe (MinerU line) ---
// These hit the gateway multipart endpoints directly. The gateway branches on
// parse_engine: mineru_cloud → corpus line; vision_layout_35b → 35B line.

export async function probePdf(file: File): Promise<any> {
  const fd = new FormData();
  fd.append("file", file);
  return studioFetch("/api/v1/studio/import/pdf-probe", { method: "POST", rawBody: fd });
}

/** 预上传 PDF 到 MinIO staging，返回真实 minio_key + 页数（AI 方案入口用，不建任务）。 */
export async function stagePdf(
  file: File,
  line: "mineru" | "35b" = "mineru",
): Promise<{
  minio_key: string;
  filename: string;
  size: number;
  page_count: number | null;
}> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("line", line);
  return studioFetch("/api/v1/studio/import/stage", { method: "POST", rawBody: fd });
}

export async function importPdf(form: FormData): Promise<any> {
  return studioFetch("/api/v1/studio/import/pdf", { method: "POST", rawBody: form });
}
