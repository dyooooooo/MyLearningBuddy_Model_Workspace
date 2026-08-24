import { marked } from "marked";
import { agent, type AgentJob, type AgentTraceStep, type ChatAttachment } from "./api";
import { el } from "./util/dom";
import { sanitizeHtml } from "./util/html-sanitize";

marked.setOptions({ gfm: true, breaks: true });

export function renderAgentTurn(root: HTMLElement, job: AgentJob, replay = false): void {
  const openThink = Boolean(root.querySelector(".kb-turn-think[open]"));
  const folded = Boolean(root.querySelector(".kb-turn-assistant.is-folded"));
  const openTools = new Set(
    [...root.querySelectorAll<HTMLDetailsElement>(".kb-turn-tool[open]")].map((n) => n.dataset.tool || ""),
  );
  const denyEarly = hookDenyText(job.trace || []);
  const visual = denyEarly && job.status === "running" ? "failed" : job.status;
  root.className = `kb-turn is-${visual}${replay ? " kb-turn--replay" : ""}`;
  root.replaceChildren();

  const payload = job.payload || {};
  const user = String(payload.message || payload.problem_text || "").trim();
  if (user || attachmentsOf(payload).length) {
    const u = el("div", "kb-turn-user");
    const atts = attachmentsOf(payload);
    if (atts.length) u.append(attachRow(atts));
    if (user) u.append(el("p", "", user));
    const meta: string[] = [];
    const mode = String(payload.mode || "");
    if (mode && mode !== "agent") meta.push(mode === "plan" ? "Plan" : mode === "ask" ? "Ask" : mode);
    if (payload.model) meta.push(String(payload.model));
    if (payload.student_answer) meta.push(`学生作答：${String(payload.student_answer)}`);
    if (payload.textbook_hint) meta.push(`书名提示：${String(payload.textbook_hint)}`);
    if (meta.length) u.append(el("p", "muted kb-turn-mode", meta.join(" · ")));
    u.append(restoreBtn(job));
    root.append(u);
  }

  const deny = denyEarly;
  const { thinks, tools, draft } = collapseTrace(job.trace || []);
  if (job.status === "completed" || job.status === "failed" || deny) {
    for (const tool of tools) {
      if (tool.status === "running") {
        tool.status = job.status === "completed" && !deny ? "completed" : "failed";
      }
    }
  }
  if (thinks.length || tools.length) {
    const act = el("div", "kb-turn-activity");
    if (thinks.length) {
      const d = el("details", "kb-turn-think") as HTMLDetailsElement;
      d.open = openThink || (job.status === "running" && !tools.length);
      const sum = el("summary", "");
      sum.append(el("span", "kb-turn-thinkmark", "✦"));
      sum.append(el("span", "", thinks.length > 1 ? `思考 · ${thinks.length}` : "思考"));
      d.append(sum, el("pre", "kb-turn-thinkbody", thinks.join("\n\n")));
      act.append(d);
    }
    for (const tool of tools) {
      const d = el("details", `kb-turn-tool is-${tool.status}`) as HTMLDetailsElement;
      d.dataset.tool = tool.name;
      d.open = openTools.has(tool.name);
      const sum = el("summary", "");
      sum.append(el("span", "kb-turn-toolmark", tool.status === "running" ? "◌" : tool.status === "failed" ? "✕" : "✓"));
      sum.append(el("span", "", toolLabel(tool)));
      for (const file of tool.files) sum.append(el("code", "kb-turn-file", file));
      d.append(sum);
      if (tool.detail) d.append(el("pre", "kb-turn-toolbody", prettyDetail(tool.detail)));
      act.append(d);
    }
    root.append(act);
  }

  if (job.status === "queued") {
    root.append(el("p", "kb-turn-live muted", "排队…"));
  } else if (job.status === "running" && !deny) {
    const live = draft.trim();
    if (live) {
      const p = el("div", "kb-turn-assistant is-live kb-md");
      p.innerHTML = chatMarkdown(live);
      enhanceCode(p);
      root.append(p);
    }
    const n = (job.trace || []).length;
    const wait = el("p", "kb-turn-live muted");
    wait.append(
      el("span", "kb-dock-ring is-inline"),
      el("span", "", n ? `正在运行 · 已 ${n} 步` : "正在运行…"),
    );
    root.append(wait);
  } else if (job.status === "cancelled") {
    root.append(el("p", "kb-turn-live muted", "已停止"));
  }

  appendResult(root, job, folded);
  const errText = job.status === "cancelled" ? "" : humanizeJobError(job.error || "") || deny;
  if (errText) root.append(el("p", "kb-turn-err", errText));
  if (job.status === "completed" || job.status === "failed" || job.status === "cancelled" || deny) {
    root.append(actionBar(job));
  }
}

function appendResult(root: HTMLElement, job: AgentJob, keepFolded: boolean): void {
  if (job.status !== "completed") return;
  const res = job.result;
  if (!res) return;

  if (job.kind === "grade") {
    const missing = Boolean(res.book_missing);
    const correct = res.correct;
    let tone = "is-pending";
    let label = String(res.verdict || "已完成");
    if (missing) {
      tone = "is-missing";
      label = String(res.verdict || "教材未覆盖");
    } else if (correct === true) {
      tone = "is-ok";
      label = String(res.verdict || "对");
    } else if (correct === false) {
      tone = "is-wrong";
      label = String(res.verdict || "错");
    }
    root.append(el("p", `kb-agent-verdict-main ${tone}`, label));
    const book = (res.textbook && typeof res.textbook === "object" ? res.textbook : {}) as Record<string, unknown>;
    const cite = [book.title, book.chapter, book.section, examplesOf(book.examples)].filter(Boolean).join(" · ");
    if (cite) {
      const p = el("p", "muted");
      p.textContent = `依据：${cite}`;
      root.append(p);
    }
  }

  const body = String(res.reply || res.correct_solution || "").trim();
  const notes = String(res.notes || "").trim();
  const md = [body, notes && notes !== body ? notes : ""].filter(Boolean).join("\n\n");
  if (!md) return;
  const a = el("div", "kb-turn-assistant kb-md");
  a.innerHTML = chatMarkdown(md);
  enhanceCode(a);
  const btn = el("button", "kb-turn-fold hidden", "展开") as HTMLButtonElement;
  btn.type = "button";
  btn.onclick = () => {
    a.classList.toggle("is-folded");
    btn.textContent = a.classList.contains("is-folded") ? "展开" : "收起";
  };
  root.append(a, btn);
  requestAnimationFrame(() => {
    if (a.scrollHeight > 360) {
      if (keepFolded || !root.querySelector(".kb-turn-fold:not(.hidden)")) {
        a.classList.add("is-folded");
      }
      btn.classList.remove("hidden");
      btn.textContent = a.classList.contains("is-folded") ? "展开" : "收起";
    }
  });
}

function collapseTrace(steps: AgentTraceStep[]): {
  thinks: string[];
  tools: { name: string; status: string; files: string[]; detail: string }[];
  draft: string;
} {
  const thinks: string[] = [];
  const tools: { name: string; status: string; files: string[]; detail: string }[] = [];
  let draft = "";
  for (const step of steps) {
    if (step.kind === "think") {
      if (step.detail) thinks.push(step.detail.trim());
      continue;
    }
    if (step.kind === "answer") {
      if (step.title === "作答中" && step.detail) draft += step.detail;
      continue;
    }
    const parsed = parseTool(step);
    const existing = [...tools].reverse().find((t) => t.name === parsed.name);
    if (existing) {
      existing.status = parsed.status;
      if (parsed.file && !existing.files.includes(parsed.file)) existing.files.push(parsed.file);
      if (parsed.detail) existing.detail = parsed.detail;
      continue;
    }
    tools.push({
      name: parsed.name,
      status: parsed.status,
      files: parsed.file ? [parsed.file] : [],
      detail: parsed.detail,
    });
  }
  return { thinks, tools, draft };
}

function parseTool(step: AgentTraceStep): { name: string; status: string; file: string; detail: string } {
  const title = (step.title || step.kind || "tool").trim();
  const m = title.match(/^(\S+)\s+(running|completed|failed)$/i);
  return {
    name: m ? m[1] : title,
    status: m ? m[2].toLowerCase() : "completed",
    file: extractFile(step.detail),
    detail: step.detail || "",
  };
}

function toolLabel(tool: { name: string; status: string; files?: string[] }): string {
  const key = tool.name.toLowerCase();
  const map: Record<string, string> = {
    read: "读取",
    search: "搜索",
    search_md: "搜索教材",
    open_pdf: "打开 PDF",
    open_pdf_page: "打开 PDF",
    list_config: "查看配置",
    write_tool: "写入工具",
    write_skill: "写入技能",
    write_rule: "写入规则",
    reload_config: "重载配置",
    grep: "搜索",
    edit: "编辑",
    write: "写入",
  };
  const n = tool.files?.length || 0;
  const label = n > 1 ? `${map[key] || tool.name} · ${n}` : map[key] || tool.name;
  return tool.status === "running" ? `${label}…` : label;
}

function enhanceCode(root: HTMLElement): void {
  for (const pre of [...root.querySelectorAll("pre")]) {
    if (pre.parentElement?.classList.contains("kb-md-prewrap")) continue;
    const wrap = el("div", "kb-md-prewrap");
    const btn = el("button", "kb-md-copy", "复制") as HTMLButtonElement;
    btn.type = "button";
    btn.onclick = () => {
      void navigator.clipboard.writeText(pre.textContent || "");
      btn.textContent = "已复制";
      window.setTimeout(() => {
        btn.textContent = "复制";
      }, 1200);
    };
    pre.replaceWith(wrap);
    wrap.append(btn, pre);
  }
}

function extractFile(detail: string): string {
  const raw = (detail || "").trim();
  if (!raw) return "";
  try {
    const obj = JSON.parse(raw) as { path?: string; file?: string };
    const p = obj.path || obj.file || "";
    if (p) return p.split("/").filter(Boolean).slice(-2).join("/");
  } catch {
    const m = raw.match(/[\w./-]+\.(md|mdc|py|json|yaml|pdf)\b/i);
    if (m) return m[0].split("/").slice(-2).join("/");
  }
  return "";
}

function prettyDetail(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function chatMarkdown(text: string): string {
  return sanitizeHtml(marked.parse(text || "", { async: false }) as string);
}

function attachmentsOf(payload: Record<string, unknown>): ChatAttachment[] {
  const raw = payload.attachments;
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((row): row is ChatAttachment => Boolean(row && typeof row === "object"))
    .map((row) => ({
      kind: String(row.kind || "file"),
      name: String(row.name || ""),
      path: row.path ? String(row.path) : undefined,
    }));
}

function attachRow(items: ChatAttachment[]): HTMLElement {
  const row = el("div", "kb-turn-atts");
  for (const item of items) {
    const chip = el("span", "kb-turn-att");
    if (item.kind === "image" && item.name && item.path) {
      const img = document.createElement("img");
      img.className = "kb-turn-att-img";
      img.alt = item.name;
      img.src = agent.inboxSrc(item.name);
      chip.append(img);
    }
    chip.append(el("span", "", item.name || "附件"));
    row.append(chip);
  }
  return row;
}

function checkpointOf(job: AgentJob): string {
  return String(job.result?.checkpoint || job.payload?.checkpoint || "").trim();
}

function restoreBtn(job: AgentJob): HTMLButtonElement {
  const checkpoint = checkpointOf(job);
  const btn = iconBtn("restore", "restore checkpoint", ICONS.restore);
  btn.classList.add("kb-turn-user-restore");
  btn.disabled = !checkpoint;
  btn.onclick = async () => {
    if (!checkpoint) return;
    if (!window.confirm("Restore checkpoint？工作区回到这一轮开始前，聊天记录会留下。")) return;
    const { hardReload } = await import("./live");
    btn.disabled = true;
    try {
      await agent.restoreCheckpoint(checkpoint);
      hardReload();
    } catch (e) {
      btn.disabled = false;
      window.alert((e as Error).message || "恢复失败");
    }
  };
  return btn;
}

function actionBar(job: AgentJob): HTMLElement {
  const bar = el("div", "kb-turn-actions");
  const reply = replyText(job);
  const checkpoint = checkpointOf(job);

  const copyBtn = iconBtn("copy", "复制", ICONS.copy);
  copyBtn.disabled = !reply;
  copyBtn.onclick = async () => {
    if (!reply) return;
    try {
      await navigator.clipboard.writeText(reply);
    } catch {
      return;
    }
    copyBtn.classList.add("is-done");
    copyBtn.title = "已复制";
    window.setTimeout(() => {
      copyBtn.classList.remove("is-done");
      copyBtn.title = "复制";
    }, 1200);
  };
  bar.append(copyBtn);

  const forkBtn = iconBtn("fork", "分叉", ICONS.fork);
  forkBtn.disabled = !checkpoint;
  forkBtn.onclick = async () => {
    if (!checkpoint) return;
    const { hardReload, rememberState } = await import("./live");
    forkBtn.disabled = true;
    try {
      const name = `turn-${checkpoint.slice(0, 8)}`;
      const out = await agent.forkCheckpoint(checkpoint, name);
      if (out.conversation_id) {
        try {
          window.localStorage.setItem("kb.agent.conversation_id", out.conversation_id);
        } catch {
          /* ignore */
        }
        rememberState({ conversationId: out.conversation_id });
        window.dispatchEvent(
          new CustomEvent("kb:open-conversation", { detail: { id: out.conversation_id, title: out.title } }),
        );
      }
      hardReload();
    } catch (e) {
      forkBtn.disabled = false;
      window.alert((e as Error).message || "分叉失败");
    }
  };
  bar.append(forkBtn);
  return bar;
}

function replyText(job: AgentJob): string {
  const res = job.result || {};
  return String(res.reply || res.correct_solution || res.notes || job.payload?.message || "").trim();
}

function iconBtn(kind: string, title: string, svg: string): HTMLButtonElement {
  const btn = el("button", `kb-turn-act kb-turn-act--${kind}`) as HTMLButtonElement;
  btn.type = "button";
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.innerHTML = svg;
  return btn;
}

const ICONS = {
  copy: `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="2.5" y="2.5" width="8" height="8" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>`,
  restore: `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.2 8A4.8 4.8 0 1 0 4.6 4.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M2.2 2.8v3.1h3.1" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  fork: `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="4" cy="3.2" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="12.8" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="12" cy="8" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M4 4.8v7M4 8h6.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`,
};

function examplesOf(raw: unknown): string {
  if (!Array.isArray(raw) || !raw.length) return "";
  return raw.map(String).join("、");
}

export function humanizeJobError(raw: string): string {
  const t = (raw || "").trim();
  if (!t) return "";
  if (/桥接超时|没有回包|任务超时|权限拒绝|本地桥接失败|桥接认证/.test(t)) return t;
  const low = t.toLowerCase();
  if (low.includes("readtimeout") || low.includes("bridge request timed out")) {
    return "Cursor 桥接超时，云端或本地桥没有回包";
  }
  if (low.includes("bridge request failed") || low.includes("bridge exited")) {
    return "Cursor 本地桥接失败，请重试";
  }
  if (low.includes("missing value for --tool-callback-auth-token") || low.includes("store-callback-auth-token")) {
    return "Cursor 桥接认证参数异常，请重试";
  }
  return t;
}

export function hookDenyText(steps: AgentTraceStep[]): string {
  for (const step of steps) {
    const blob = `${step.title || ""}\n${step.detail || ""}`;
    const low = blob.toLowerCase();
    const denied =
      (low.includes("permission") && low.includes("deny")) ||
      low.includes("permission_denied") ||
      low.includes("权限拒绝");
    if (!denied) continue;
    const m =
      blob.match(/user_message["']?\s*[:=]\s*["']([^"']+)/i) ||
      blob.match(/权限拒绝[：:]\s*(.+)/);
    if (m) return m[1].startsWith("权限拒绝") ? m[1].trim() : `权限拒绝：${m[1].trim()}`;
    return step.detail?.trim() || "权限拒绝：钩子不允许这次操作";
  }
  return "";
}
