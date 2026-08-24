// 35B layout-import line API client.
//
// FULLY ISOLATED from the corpus client (./api/corpus.ts):
//   - different proxy base: /api/v1/studio/proxy35b/* → buddy-api /admin/import35b/*
//   - its own job/page types
//   - no shared endpoints, no shared types
// The only overlap with the corpus line is the multipart upload endpoint
// (/api/v1/studio/import/pdf), where the gateway itself branches on parse_engine
// and routes 35B uploads to the isolated import35b API + MinIO import35b/ prefix.

import { studioFetch, type HttpMethod } from "./http";

const PROXY35B = "/api/v1/studio/proxy35b";
const ADMIN35B = "/api/v1/admin/import35b"; // origin-agnostic paths baked into stored HTML

export interface Import35bJob {
  id: string;
  textbook_key: string;
  status: string;
  subject: string;
  grade_band: string;
  label: string;
  minio_key: string;
  page_count: number;
  page_limit: number | null;
  profile_id: string;
  error: string;
  meta: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface Import35bPageInfo {
  page_no: number;
  chars: number;
}

export interface Import35bPages {
  job_id: string;
  textbook_key: string;
  status: string;
  page_count: number;
  pages: Import35bPageInfo[];
}

export interface Import35bFeedbackItem {
  id: string;
  job_id: string;
  page_no: number;
  body: string;
  tags: string[];
  block_id: string;
  screenshot_key: string;
  status: string;
  pack_path: string;
  t1_snapshot: Record<string, unknown>[];
  created_at: string;
}

export interface Import35bFeedbackList {
  job_id: string;
  open_count: number;
  items: Import35bFeedbackItem[];
}

export interface ChangeEntry {
  transition: string;
  stage_label: string;
  block_id: string;
  path: string;
  kind: string;
  before: string;
  after: string;
  reason: string;
  summary_zh: string;
}

export interface PageDebugChangelog {
  page_no: number;
  total_changes: number;
  summary_zh: string;
  by_transition: Record<
    string,
    { count: number; changes: ChangeEntry[]; stage_label: string }
  >;
}

export interface PageDebugIndex {
  job_id: string;
  page_no: number;
  debug_base: string;
  artifacts: Record<string, { available: boolean; size_bytes: number }>;
  meta: Record<string, unknown> | null;
  changelog_summary: {
    total_changes: number;
    summary_zh: string;
    by_transition: Record<string, number>;
  };
}

export type PageDebugArtifactId =
  | "raw_model"
  | "post_repair"
  | "post_mfr"
  | "post_crop"
  | "blueprint"
  | "html"
  | "ssr_report"
  | "meta";

async function call(method: HttpMethod, path: string, body?: unknown): Promise<any> {
  return studioFetch(`${PROXY35B}/${path}`, { method, body });
}

/** Rewrite origin-agnostic buddy-api admin asset/css URLs (baked into stored
 * page HTML) to the gateway proxy origin so figures/styles load inside Studio. */
function rewriteAssetUrls(html: string): string {
  return html.split(ADMIN35B).join(PROXY35B);
}

export const import35b = {
  jobs: (): Promise<Import35bJob[]> => call("GET", "jobs"),
  job: (id: string): Promise<Import35bJob> => call("GET", `jobs/${id}`),
  pages: (id: string): Promise<Import35bPages> => call("GET", `jobs/${id}/pages`),
  deleteJob: (id: string): Promise<any> => call("DELETE", `jobs/${id}`),

  async pageHtml(
    id: string,
    pageNo: number,
    opts?: { bustCache?: boolean },
  ): Promise<string> {
    const q = opts?.bustCache ? `?_ts=${Date.now()}` : "";
    const html = await studioFetch<string>(
      `${PROXY35B}/jobs/${id}/page/${pageNo}/html${q}`,
    );
    return rewriteAssetUrls(typeof html === "string" ? html : String(html));
  },

  /** Genuine compiled page HTML (no asset-URL rewrite) — for source inspection. */
  async pageHtmlRaw(id: string, pageNo: number): Promise<string> {
    const html = await studioFetch<string>(`${PROXY35B}/jobs/${id}/page/${pageNo}/html`);
    return typeof html === "string" ? html : String(html);
  },

  /** Structured output (blueprint) for a page/block — for source inspection. */
  pageBlueprint: (id: string, pageNo: number, blockId?: string): Promise<any> =>
    call(
      "GET",
      `jobs/${id}/page/${pageNo}/blueprint${blockId ? `?block_id=${encodeURIComponent(blockId)}` : ""}`,
    ),

  pageDebugIndex: (id: string, pageNo: number): Promise<PageDebugIndex> =>
    call("GET", `jobs/${id}/page/${pageNo}/debug`),

  pageDebugChangelog: (id: string, pageNo: number): Promise<PageDebugChangelog> =>
    call("GET", `jobs/${id}/page/${pageNo}/debug/changelog`),

  pageDebugImageUrl: (id: string, pageNo: number): string =>
    `${PROXY35B}/jobs/${id}/page/${pageNo}/debug/page-image`,

  async pageDebugArtifact(
    id: string,
    pageNo: number,
    artifact: PageDebugArtifactId,
  ): Promise<unknown> {
    return studioFetch(`${PROXY35B}/jobs/${id}/page/${pageNo}/debug/${artifact}`);
  },

  /** Stylesheet href for the isolated 35B rendering (its own textbook CSS). */
  cssHref: (): string => `${PROXY35B}/textbook.css`,

  // --- User feedback + Cursor Repair Pack ---
  listFeedback: (id: string, status?: string): Promise<Import35bFeedbackList> =>
    call("GET", `jobs/${id}/feedback${status ? `?status=${status}` : ""}`),

  async createFeedback(
    id: string,
    pageNo: number,
    form: FormData,
  ): Promise<{ ok: boolean; feedback: Import35bFeedbackItem }> {
    return studioFetch(`${PROXY35B}/jobs/${id}/pages/${pageNo}/feedback`, {
      method: "POST",
      rawBody: form,
    });
  },

  buildCursorPack: (feedbackId: string): Promise<{ pack_path?: string; absolute_path?: string }> =>
    call("POST", `feedback/${feedbackId}/cursor-pack`),

  reprocessPage: (
    id: string,
    pageNo: number,
    body?: { mode?: "full" | "from_raw"; reason?: string; feedback_id?: string | null },
  ): Promise<any> =>
    call("POST", `jobs/${id}/pages/${pageNo}/reprocess`, body || { mode: "full" }),

  refineBlock: (
    id: string,
    pageNo: number,
    blockId: string,
    body?: { note?: string; feedback_id?: string | null },
  ): Promise<any> =>
    call(
      "POST",
      `jobs/${id}/pages/${pageNo}/blocks/${encodeURIComponent(blockId)}/refine`,
      body || {},
    ),

  patchFeedback: (
    feedbackId: string,
    body: { status?: string; resolution_note?: string },
  ): Promise<any> => call("PATCH", `feedback/${feedbackId}`, body),

  reportIssue: (
    id: string,
    pageNo: number,
    body: { block_id?: string; description: string },
  ): Promise<any> => call("POST", `jobs/${id}/pages/${pageNo}/report`, body),
};

/**
 * Upload a PDF for the 35B line. Uses the shared gateway multipart endpoint but
 * pins parse_engine=vision_layout_35b so the gateway routes it into the
 * isolated import35b API + MinIO import35b/ prefix.
 */
export async function importPdf35b(params: {
  file: File;
  subject?: string;
  gradeBand?: string;
  label?: string;
  pageLimit?: number;
  profileId?: string;
}): Promise<{ id: string }> {
  const fd = new FormData();
  fd.append("file", params.file);
  fd.append("subject", params.subject || "math");
  fd.append("grade_band", params.gradeBand || "hs");
  fd.append("label", params.label || "");
  fd.append("parse_engine", "vision_layout_35b");
  const eid = (params.profileId || "").trim();
  if (!eid) throw new Error("edition_id / profileId 必填");
  fd.append("edition_id", eid);
  fd.append("profile_id", eid);
  if (params.pageLimit && params.pageLimit > 0) {
    fd.append("page_limit", String(params.pageLimit));
  }
  return studioFetch("/api/v1/studio/import/pdf", { method: "POST", rawBody: fd });
}
