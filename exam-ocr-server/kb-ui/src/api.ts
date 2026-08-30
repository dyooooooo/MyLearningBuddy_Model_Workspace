import { getStudioToken, promptRelogin } from "./auth/studioToken";

export type KbBook = {
  id: string;
  label: string;
  filename: string;
  subject: string;
  grade_band: string;
  category: string;
  textbook_hint: string;
  minio_key: string;
  status: string;
  page_count: number;
  error: string;
  is_default: boolean;
  toc_entry_count: number;
  created_at: string;
  updated_at: string;
};

export type KbTocEntry = {
  label: string;
  print_page: number | null;
  pdf_page?: number | null;
  is_chapter: boolean;
  level: number;
};

export type KbToc = {
  book_id: string;
  entries: KbTocEntry[];
  print_to_pdf: Record<string, number>;
  toc_pages: number[];
};

export type KbMineruOut = {
  settings: {
    api_base_url: string;
    model_version: string;
    language: string;
    enable_formula: boolean;
    enable_table: boolean;
    is_ocr: boolean;
    timeout_s: number;
    updated_at: string;
  };
  api_token_set: boolean;
  api_token: string;
};

export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getStudioToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const r = await fetch(path, { ...init, headers });
  if (r.status === 401) {
    promptRelogin();
    throw new Error("需要重新登录");
  }
  return r;
}

export async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await request(path, init);
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text.slice(0, 400) || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

export const kb = {
  list(): Promise<KbBook[]> {
    return json("/api/v1/kb/proxy/books");
  },
  get(id: string): Promise<KbBook> {
    return json(`/api/v1/kb/proxy/books/${id}`);
  },
  toc(id: string): Promise<KbToc> {
    return json(`/api/v1/kb/proxy/books/${id}/toc`);
  },
  patch(
    id: string,
    body: {
      label?: string;
      textbook_hint?: string;
      subject?: string;
      grade_band?: string;
      category?: string;
      is_default?: boolean;
    },
  ): Promise<KbBook> {
    return json(`/api/v1/kb/proxy/books/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  remove(id: string): Promise<{ deleted: string }> {
    return json(`/api/v1/kb/proxy/books/${id}`, { method: "DELETE" });
  },
  reimport(id: string): Promise<KbBook> {
    return json(`/api/v1/kb/proxy/books/${id}/reimport`, { method: "POST" });
  },
  async upload(
    file: File | null,
    opts: {
      label?: string;
      textbook_hint?: string;
      subject?: string;
      grade_band?: string;
      category?: string;
      book_id?: string;
      conversation_id?: string;
    } = {},
  ): Promise<KbBook> {
    const fd = new FormData();
    if (file) fd.append("file", file);
    fd.append("label", opts.label || "");
    fd.append("textbook_hint", opts.textbook_hint || "");
    fd.append("subject", opts.subject || "math");
    fd.append("grade_band", opts.grade_band || "hs");
    fd.append("category", opts.category || "textbook");
    if (opts.book_id) fd.append("book_id", opts.book_id);
    if (opts.conversation_id) fd.append("conversation_id", opts.conversation_id);
    return json("/api/v1/kb/import/pdf", { method: "POST", body: fd });
  },
  async stageDraft(file: File): Promise<KbBook> {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("label", file.name.replace(/\.pdf$/i, ""));
    fd.append("textbook_hint", file.name.replace(/\.pdf$/i, ""));
    fd.append("status", "draft");
    return json("/api/v1/kb/agent/library", { method: "POST", body: fd });
  },
  patchDraft(
    id: string,
    body: {
      label?: string;
      textbook_hint?: string;
      subject?: string;
      grade_band?: string;
      category?: string;
      status?: string;
      conversation_id?: string;
    },
  ): Promise<KbBook> {
    return json(`/api/v1/kb/agent/library/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  removeDraft(id: string): Promise<{ deleted: string }> {
    return json(`/api/v1/kb/agent/library/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  identify(
    id: string,
  ): Promise<
    KbBook & {
      pages_used?: number;
      preview?: string;
      preview_path?: string;
    }
  > {
    return json(`/api/v1/kb/identify/${encodeURIComponent(id)}`, { method: "POST" });
  },
  pageMarkdown(
    id: string,
    pageNo: number,
  ): Promise<{ markdown: string; prepared?: string; page_count: number }> {
    return json(`/api/v1/kb/proxy/books/${id}/pages/${pageNo}/markdown`);
  },
  surfacePaint(slot: string, payload: Record<string, unknown>): Promise<{ html: string }> {
    return json(`/api/v1/kb/agent/surfaces/${encodeURIComponent(slot)}/paint`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ payload }),
    });
  },
  pageImageSrc(id: string, pageNo: number): string {
    return `/api/v1/kb/proxy/books/${id}/pages/${pageNo}/image`;
  },
  mineruGet(): Promise<KbMineruOut> {
    return json("/api/v1/kb/settings/mineru");
  },
  mineruSave(body: Record<string, unknown>): Promise<KbMineruOut> {
    return json("/api/v1/kb/settings/mineru", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  mineruTest(body: Record<string, unknown> = {}): Promise<{ ok: boolean; message: string }> {
    return json("/api/v1/kb/settings/mineru/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
};

export type AgentTraceStep = {
  t: string;
  kind: string;
  title: string;
  detail: string;
};

export type ChatMode = "agent" | "plan" | "ask";

export type ChatAttachment = {
  kind: string;
  name: string;
  path?: string;
};

export type ChatImage = {
  data: string;
  mime_type: string;
  name: string;
};

export type AgentJob = {
  job_id: string;
  kind: string;
  status: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string;
  trace: AgentTraceStep[];
  created_at: string;
  updated_at: string;
};

export type AgentTaskEvent = {
  id: string;
  label: string;
  group: string;
};

export type AgentTask = {
  id: string;
  title: string;
  enabled: boolean;
  mode: string;
  trigger: string;
  cron: string;
  next_run: string;
  action: string;
  prompt: string;
  message: string;
  identify_message?: string;
  builtin: boolean;
  last_run_at: string;
  last_job_id: string;
  prompt_text?: string;
  last_job?: {
    job_id: string;
    kind: string;
    status: string;
    error: string;
    updated_at: string;
  };
};

export type AgentPrompt = {
  id: string;
  content: string;
  source: string;
  seed: string;
};

export type AgentHookEntry = {
  type?: string;
  command?: string;
  prompt?: string;
  model?: string;
  matcher?: string;
  failClosed?: boolean;
};

export type AgentPlugin = {
  id: string;
  name: string;
  version: string;
  description: string;
  enabled: boolean;
  parts: string[];
  path: string;
};

export type AgentCheckpoint = {
  id: string;
  time: string;
  kind: string;
  note: string;
  job_id?: string;
  conversation_id?: string;
  files?: string[];
  file_count?: number;
};

export type RestoreChange = { path: string; change: string; label: string };

export type AgentRestore = {
  id: string;
  restore_id?: string;
  files?: string[];
  changes?: RestoreChange[];
  file_count?: number;
  conversation_id?: string;
  job_id?: string;
  message?: string;
  removed_job_ids?: string[];
  kept_job_ids?: string[];
  kept_plan?: boolean;
};

export type AgentConfig = {
  generation: number;
  mcp: { mcpServers: Record<string, Record<string, unknown>> };
  skills: { id: string; content: string }[];
  rules: { id: string; content: string }[];
  tools: { tools: { id: string; path?: string; description?: string; content?: string }[] };
  prompts?: AgentPrompt[];
  hooks?: { version?: number; hooks?: Record<string, AgentHookEntry[]>; scripts?: { id: string; content: string }[] };
  plugins?: AgentPlugin[];
  plugin_allowlist?: string[];
};

const AGENT = "/api/v1/kb/agent";

export const agent = {
  config(): Promise<AgentConfig> {
    return json(`${AGENT}/config`);
  },
  putMcp(mcpServers: Record<string, Record<string, unknown>>): Promise<unknown> {
    return json(`${AGENT}/config/mcp`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mcpServers }),
    });
  },
  putSkill(id: string, content: string): Promise<unknown> {
    return json(`${AGENT}/config/skills/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  },
  deleteSkill(id: string): Promise<unknown> {
    return json(`${AGENT}/config/skills/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  putRule(id: string, content: string): Promise<unknown> {
    return json(`${AGENT}/config/rules/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  },
  deleteRule(id: string): Promise<unknown> {
    return json(`${AGENT}/config/rules/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  putTool(id: string, body: { path?: string; description?: string; content?: string }): Promise<unknown> {
    return json(`${AGENT}/config/tools/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  deleteTool(id: string): Promise<unknown> {
    return json(`${AGENT}/config/tools/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  reload(): Promise<{ generation: number }> {
    return json(`${AGENT}/config/reload`, { method: "POST" });
  },
  putPrompt(name: string, content: string): Promise<unknown> {
    return json(`${AGENT}/config/prompts/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  },
  deletePrompt(name: string): Promise<unknown> {
    return json(`${AGENT}/config/prompts/${encodeURIComponent(name)}`, { method: "DELETE" });
  },
  putHooks(body: { version?: number; hooks: Record<string, AgentHookEntry[]> }): Promise<unknown> {
    return json(`${AGENT}/config/hooks`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  putHookScript(name: string, content: string): Promise<unknown> {
    return json(`${AGENT}/config/hooks/scripts/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  },
  deleteHookScript(name: string): Promise<unknown> {
    return json(`${AGENT}/config/hooks/scripts/${encodeURIComponent(name)}`, { method: "DELETE" });
  },
  async uploadPlugin(file: File): Promise<AgentPlugin> {
    const fd = new FormData();
    fd.append("file", file);
    return json(`${AGENT}/config/plugins/upload`, { method: "POST", body: fd });
  },
  installPluginGit(url: string, ref = ""): Promise<AgentPlugin> {
    return json(`${AGENT}/config/plugins/git`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, ref }),
    });
  },
  setPluginEnabled(id: string, enabled: boolean): Promise<AgentPlugin> {
    return json(`${AGENT}/config/plugins/${encodeURIComponent(id)}/enabled`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  },
  deletePlugin(id: string): Promise<unknown> {
    return json(`${AGENT}/config/plugins/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  checkpoints(limit = 40): Promise<{ checkpoints: AgentCheckpoint[] }> {
    return json(`${AGENT}/checkpoints?limit=${limit}`);
  },
  createCheckpoint(note = ""): Promise<AgentCheckpoint> {
    return json(`${AGENT}/checkpoints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "manual", note }),
    });
  },
  checkpointDiff(ref: string): Promise<{ id: string; text: string; files: string[] }> {
    return json(`${AGENT}/checkpoints/${encodeURIComponent(ref)}/diff`);
  },
  restoreCheckpoint(ref: string): Promise<AgentRestore> {
    return json(`${AGENT}/checkpoints/${encodeURIComponent(ref)}/restore`, { method: "POST" });
  },
  decideApproval(
    reqId: string,
    choice: "grant" | "refuse" | "revise",
    jobId = "",
    note = "",
  ): Promise<Record<string, unknown>> {
    return json(`${AGENT}/approvals/${encodeURIComponent(reqId)}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ choice, job_id: jobId, note }),
    });
  },
  forkCheckpoint(ref: string, name: string): Promise<{ conversation_id: string; title?: string; branch: string }> {
    return json(`${AGENT}/checkpoints/${encodeURIComponent(ref)}/fork`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  },
  jobs(): Promise<{ jobs: AgentJob[] }> {
    return json(`${AGENT}/jobs`);
  },
  job(id: string): Promise<AgentJob> {
    return json(`${AGENT}/jobs/${encodeURIComponent(id)}`);
  },
  cancelJob(id: string): Promise<AgentJob> {
    return json(`${AGENT}/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
  },
  books(): Promise<{ files: { path: string; name: string; kind: string }[] }> {
    return json(`${AGENT}/books`);
  },
  models(): Promise<{
    models: {
      id: string;
      label: string;
      group?: string;
      parameters?: { id: string; display_name?: string; values: { value: string; display_name?: string }[] }[];
      variants?: { display_name?: string; is_default?: boolean; params: { id: string; value: string }[] }[];
    }[];
    default: string;
  }> {
    return json(`${AGENT}/models`);
  },
  inboxSrc(name: string): string {
    const token = getStudioToken();
    const q = token ? `?access_token=${encodeURIComponent(token)}` : "";
    return `${AGENT}/inbox/${encodeURIComponent(name)}${q}`;
  },
  tasks(): Promise<{ tasks: AgentTask[] }> {
    return json(`${AGENT}/tasks`);
  },
  taskEvents(): Promise<{ events: AgentTaskEvent[] }> {
    return json(`${AGENT}/tasks/events`);
  },
  task(id: string): Promise<AgentTask> {
    return json(`${AGENT}/tasks/${encodeURIComponent(id)}`);
  },
  createTask(body: {
    title: string;
    mode?: string;
    trigger?: string;
    cron?: string;
    next_run?: string;
    message?: string;
    action?: string;
    prompt?: string;
    enabled?: boolean;
  }): Promise<AgentTask> {
    return json(`${AGENT}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  patchTask(id: string, body: Partial<AgentTask>): Promise<AgentTask> {
    return json(`${AGENT}/tasks/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  cancelTask(id: string): Promise<{ task: AgentTask; cancelled_jobs: number }> {
    return json(`${AGENT}/tasks/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  runTask(id: string, body: { book_id?: string; message?: string } = {}): Promise<AgentJob> {
    return json(`${AGENT}/tasks/${encodeURIComponent(id)}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  conversations(): Promise<{
    conversations: {
      id: string;
      title: string;
      updated_at: string;
      created_at: string;
      status: string;
      turns: number;
      preview: string;
    }[];
  }> {
    return json(`${AGENT}/conversations`);
  },
  conversation(id: string): Promise<{ conversation: { id: string; title: string }; jobs: AgentJob[] }> {
    return json(`${AGENT}/conversations/${encodeURIComponent(id)}`);
  },
  chat(body: {
    message: string;
    conversation_id?: string;
    mode?: ChatMode;
    model?: string;
    plan_id?: string;
    model_params?: { id: string; value: string }[];
    images?: ChatImage[];
    files?: { data: string; name: string; mime_type: string }[];
    task_id?: string;
    trigger?: string;
  }): Promise<{ job_id: string; status: string; conversation_id?: string; title?: string }> {
    return json(`${AGENT}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  eventsUrl(): string {
    const token = getStudioToken();
    const q = token ? `?access_token=${encodeURIComponent(token)}` : "";
    return `${AGENT}/events${q}`;
  },
};
