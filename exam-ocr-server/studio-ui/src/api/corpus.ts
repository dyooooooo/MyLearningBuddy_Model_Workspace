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
  getImportPageHtml: (id: string, page: number): Promise<any> =>
    call("GET", `import/jobs/${id}/page/${page}/html`),
  rebuildImportIr: (id: string, pages?: number[]): Promise<any> =>
    call("POST", `import/jobs/${id}/rebuild-ir`, pages ? { pages } : {}),
  /** Re-run chapter/TOC structure from existing IR (purge stale TOC, keep page_import). */
  structureMerge: (
    id: string,
    body: { purge?: boolean } = { purge: true },
  ): Promise<ImportJob> =>
    call("POST", `import/jobs/${encodeURIComponent(id)}/structure-merge`, body),
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
