// Buddy Copilot API client.
//
// Routes through the gateway proxy `/api/v1/studio/copilot/*` → host-side
// buddy-copilot service (:8200, Cursor SDK orchestrator). Neutral plane —
// not owned by MinerU (/proxy) or 35B (/proxy35b).

import { getStudioToken } from "../auth/studioToken";
import { studioFetch, type HttpMethod } from "./http";

const BASE = "/api/v1/studio/copilot";

export type CopilotKind = "import_advice" | "page_assist";

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
  mode?: string;
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

  createSession: (
    kind: CopilotKind,
    payload: Record<string, unknown>,
    model = "",
  ): Promise<{ session_id: string; resumed?: boolean; import_job_id?: string | null }> =>
    call("POST", "/sessions", { kind, payload, model }),

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

  cancel: (sid: string): Promise<{ ok: boolean }> => call("POST", `/sessions/${sid}/cancel`),

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
