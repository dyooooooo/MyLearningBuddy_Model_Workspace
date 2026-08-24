// Buddy Copilot API client.
//
// Routes through the gateway proxy `/api/v1/studio/copilot/*` → host-side
// buddy-copilot service (:8200, Cursor SDK orchestrator). Neutral plane —
// not owned by MinerU (/proxy) or 35B (/proxy35b).

import { getStudioToken } from "../auth/studioToken";
import { studioFetch, type HttpMethod } from "./http";

const BASE = "/api/v1/studio/copilot";

export type CopilotKind =
  | "import_advice"
  | "ip_book"
  | "ip_triage" // 兼容旧存储；创建时服务端归一为 ip_book
  | "ip_split"
  /** 改进点抽象成跨书模式（症状 · 检测器 · 处置 · 生效面） */
  | "pattern_abstract";

/** 改进点一书一对话的意图（按钮只切换意图，不新建平行会话）。 */
export type IpBookIntent = "triage" | "split_all" | "split_one" | "aggregate";

export type CopilotMessage = {
  seq: number;
  role:
    | "user"
    | "assistant"
    | "system"
    | "status"
    | "plan"
    | "approval"
    | "step"
    | "verify"
    | "result"
    | "diff"
    | "diff_resolved"
    | "todos"
    | "approval_resolved"
    | "import_event";
  content: any;
  created_at: number;
};

/** Copilot → 导入面板：打开指定任务预览。 */
export type CopilotImportEvent = {
  event: "import_started" | "import_progress" | "import_done" | "import_failed" | "reprocess_done";
  line?: "mineru" | "35b" | string;
  job_id?: string;
  status?: string;
  pages_ready?: number | string;
  page_limit?: number;
  edition_id?: string;
  page_no?: number;
  ok?: boolean;
  error?: string;
  pipeline_phase?: string;
  /** 自动改第几轮（找症状 → 按手册改 → 验活） */
  autofix_round?: number;
  autofix_remaining?: number;
  autofix_done?: boolean;
  batch_types?: string[];
  batch_n?: number;
  known_n?: number;
  unknown_n?: number;
  park_reason?: string;
  symptom_rows?: Array<{
    suspect_type: string;
    known?: boolean;
    count?: number;
    pages?: number[];
    playbook?: string;
    remediation_kind?: string;
    landing_target?: string;
  }>;
  mode?: string;
  /** 改动是否已反映到页 HTML；false 时不提示记入方案 */
  html_changed?: boolean;
  block_id?: string;
  summary?: string;
  /** 为 true 时前端弹出「是否记入当前方案」 */
  promote_prompt?: boolean;
  /** edition=非越权；pipeline=越权（需改 Pipeline 核心） */
  authority?: "edition" | "pipeline" | string;
  authority_label?: string;
};

export type CopilotSessionSummary = {
  id: string;
  kind: string;
  status: string;
  import_job_id?: string;
  /** 展示名：payload.label 或 PDF 文件名（服务端派生）。 */
  label?: string;
  created_at: number;
  updated_at: number;
};

export type CopilotPlanStep = {
  id: string;
  title: string;
  tool: string;
  args: Record<string, unknown>;
  channel: "readonly" | "cheap" | "expensive";
};

export type CopilotPlan = {
  intent: string;
  summary: string;
  risk: string;
  risk_note: string;
  steps: CopilotPlanStep[];
  verification: CopilotPlanStep[];
  user_options?: string;
};

async function call(
  method: "GET" | "POST" | "DELETE",
  path: string,
  body?: unknown,
): Promise<any> {
  // Paths may already include query (messages?after_seq=)
  const [p, q] = path.split("?");
  const query: Record<string, string> = {};
  if (q) {
    new URLSearchParams(q).forEach((v, k) => {
      query[k] = v;
    });
  }
  return studioFetch(`${BASE}${p}`, {
    method: method as HttpMethod,
    body,
    query: Object.keys(query).length ? query : undefined,
  });
}

export type CopilotModel = { id: string; label: string };

export const copilot = {
  health: (): Promise<{ status: string; problems: string[] }> => call("GET", "/health"),

  listModels: (): Promise<{ models: CopilotModel[]; default: string }> =>
    call("GET", "/models"),

  /** 导入任务被删/定稿清理后：删掉绑在它上面的会话（网关级联入口）。 */
  cascadeByImportJob: (jobId: string): Promise<any> =>
    studioFetch(
      `/api/v1/studio/copilot/cascade-by-import-job/${encodeURIComponent(jobId)}`,
      { method: "POST" },
    ),

  createSession: (
    kind: CopilotKind,
    payload: Record<string, unknown>,
    model = "",
    opts: { auto_start?: boolean } = {},
  ): Promise<{
    session_id: string;
    resumed?: boolean;
    import_job_id?: string | null;
    auto_start?: boolean;
  }> =>
    call("POST", "/sessions", {
      kind,
      payload,
      model,
      auto_start: opts.auto_start !== false,
    }),

  getMessages: (
    sid: string,
    afterSeq: number,
  ): Promise<{ status: string; messages: CopilotMessage[] }> =>
    call("GET", `/sessions/${sid}/messages?after_seq=${afterSeq}`),

  sendMessage: (sid: string, text: string): Promise<{ ok: boolean }> =>
    call("POST", `/sessions/${sid}/messages`, { text }),

  decide: (
    sid: string,
    approvalId: string,
    approved: boolean,
    note = "",
    remember = false,
  ): Promise<{ ok: boolean }> =>
    call("POST", `/sessions/${sid}/approvals`, {
      approval_id: approvalId,
      approved,
      note,
      remember,
    }),

  cancel: (
    sid: string,
    discardFiles = true,
  ): Promise<{ ok: boolean; discard_files?: boolean }> =>
    call(
      "POST",
      discardFiles
        ? `/sessions/${sid}/cancel`
        : `/sessions/${sid}/cancel?discard_files=false`,
    ),

  /** 保留本轮文件改动（类似 Cursor Keep）。 */
  keepFiles: (sid: string): Promise<{ ok: boolean; action?: string; error?: string }> =>
    call("POST", `/sessions/${sid}/fs/keep`),

  /** 放弃并回退到本轮执行前（类似 Cursor Undo / git checkout）。 */
  discardFiles: (
    sid: string,
  ): Promise<{ ok: boolean; action?: string; restored?: string[]; error?: string }> =>
    call("POST", `/sessions/${sid}/fs/discard`),

  deleteSession: (sid: string): Promise<{ ok: boolean; deleted?: string[] }> =>
    call("DELETE", `/sessions/${sid}`),

  /** 按导入任务删除绑定的 AI 对话（与任务级联）。 */
  deleteByImportJob: (
    jobId: string,
  ): Promise<{ ok: boolean; deleted?: string[]; import_job_id?: string }> =>
    call("DELETE", `/sessions/by-import-job/${encodeURIComponent(jobId)}`),

  listSessions: (
    importJobId = "",
  ): Promise<{ sessions: CopilotSessionSummary[] }> =>
    call(
      "GET",
      importJobId
        ? `/sessions?import_job_id=${encodeURIComponent(importJobId)}`
        : "/sessions",
    ),

  /** 取绑定某导入任务的会话（最多一条）。 */
  findByImportJob: async (jobId: string): Promise<CopilotSessionSummary | null> => {
    const jid = String(jobId || "").trim();
    if (!jid) return null;
    const { sessions } = await call(
      "GET",
      `/sessions?import_job_id=${encodeURIComponent(jid)}`,
    );
    const list = Array.isArray(sessions) ? sessions : [];
    return list[0] || null;
  },

  /** 按书指纹找回改进点会话（默认 ip_book；找不到再试旧 kind）。 */
  findByFingerprint: async (
    fingerprintHash: string,
    kind: CopilotKind = "ip_book",
  ): Promise<CopilotSessionSummary | null> => {
    const fp = String(fingerprintHash || "").trim();
    if (!fp) return null;
    const order: CopilotKind[] =
      kind === "ip_book" || kind === "ip_triage" || kind === "ip_split"
        ? ["ip_book", "ip_triage", "ip_split"]
        : [kind];
    for (const k of order) {
      const out = await call(
        "GET",
        `/sessions/by-fingerprint/${encodeURIComponent(fp)}?kind=${encodeURIComponent(k)}`,
      );
      const sess = (out?.session as CopilotSessionSummary) || null;
      if (sess?.id) return sess;
    }
    return null;
  },

  getSession: (
    sid: string,
  ): Promise<CopilotSessionSummary & { running?: boolean; payload?: Record<string, unknown> }> =>
    call("GET", `/sessions/${sid}`),

  /** SSE 事件流；EventSource 无法带 Header，用 access_token 查询参数。 */
  eventsUrl: (sid: string, afterSeq: number): string => {
    const tok = encodeURIComponent(getStudioToken());
    return `${BASE}/sessions/${sid}/events?after_seq=${afterSeq}&access_token=${tok}`;
  },

  uploadAttachment: async (
    sid: string,
    file: File,
  ): Promise<{ path: string; filename: string; size: number }> => {
    const fd = new FormData();
    fd.append("file", file, file.name || "attachment.png");
    return studioFetch(`${BASE}/sessions/${sid}/attachments`, {
      method: "POST",
      rawBody: fd,
    });
  },
};
