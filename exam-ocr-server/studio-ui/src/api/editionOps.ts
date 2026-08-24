/** Edition Ops API — shared config plane via /proxy-editions (not MinerU/35B). */

import { studioFetch, type HttpMethod } from "./http";

const PROXY = "/api/v1/studio/proxy-editions";

async function call<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
  query?: Record<string, string>,
): Promise<T> {
  return studioFetch<T>(`${PROXY}/${path.replace(/^\//, "")}`, {
    method: method as HttpMethod,
    body,
    query,
  });
}

export type EditionSummary = {
  id: string;
  display_name: string;
  base_id: string;
  subject: string;
  grade_band: string;
  description: string;
  published_version: string;
  status: string;
  job_count: number;
  pending_corrections: number;
};

export const editionOps = {
  overview: () => call<{ edition_count: number; editions: EditionSummary[]; pending_corrections: number; seed_ids: string[]; default_seed: string }>("GET", "overview"),
  list: () => call<{ editions: EditionSummary[] }>("GET", ""),
  bundle: (id: string) => call<Record<string, unknown>>("GET", `${encodeURIComponent(id)}/bundle`),
  draft: (id: string) => call<{ edition_id: string; bundle: Record<string, unknown>; updated_at: string }>("GET", `${encodeURIComponent(id)}/draft`),
  saveDraft: (id: string, bundle: unknown, updated_by = "") =>
    call("PUT", `${encodeURIComponent(id)}/draft`, { bundle, updated_by }),
  publish: (id: string, note = "") => call("POST", `${encodeURIComponent(id)}/publish`, { note }),
  versions: (id: string) => call<{ versions: Array<{ version: string; note: string; created_at: string }> }>("GET", `${encodeURIComponent(id)}/versions`),
  fork: (source_id: string, new_id: string, display_name = "") =>
    call("POST", "fork", { source_id, new_id, display_name }),
  corrections: (edition_id?: string, status = "pending") => {
    const q: Record<string, string> = { status };
    if (edition_id) q.edition_id = edition_id;
    return call<{ items: Array<Record<string, unknown>> }>("GET", "corrections/queue", undefined, q);
  },
  /** 将改进点写入方案待审队列（不直接改正式方案，需 Edition Ops 审阅后 publish）。 */
  enqueueCorrection: (
    edition_id: string,
    candidate: {
      id: string;
      facet: string;
      severity?: string;
      statement: string;
      tags?: string[];
      /** edition=非越权（可进方案数据）；pipeline=越权（需改核心代码） */
      authority?: "edition" | "pipeline";
    },
    source = "ai_page_edit",
    source_ref = "",
  ) =>
    call<{ id?: string; corr_id?: string; status?: string }>("POST", "corrections/enqueue", {
      edition_id,
      candidate,
      source,
      source_ref,
    }),
  reviewCorrection: (id: string, action: "approve" | "reject", reviewer = "", review_note = "") =>
    call("POST", `corrections/${encodeURIComponent(id)}/review`, { action, reviewer, review_note }),
  calibrate: (edition_id: string, display_name = "", persist_fork = false, sample_bboxes?: number[][]) =>
    call("POST", "calibrate", { edition_id, display_name, persist_fork, sample_bboxes }),
  golden: (id: string) => call<{ ok: boolean; checks: unknown[]; config_sha: string }>("POST", `${encodeURIComponent(id)}/golden`),
  jobStats: () => call<{ by_edition: Record<string, number> }>("GET", "stats/jobs"),
};
