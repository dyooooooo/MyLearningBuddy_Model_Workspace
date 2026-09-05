import { marked } from "marked";
import { agent, type AgentJob, type AgentTraceStep, type ChatAttachment } from "./api";
import { collapseTrace, liveStatus, toolLabel, type CollapsedTool, type TimelineItem } from "./collapseTrace";
import { extractPlanMarkdown, looksLikePlan, titleFromMarkdown } from "./planExtract";
import { copyText } from "./copyText";
import { replyText } from "./turnReply";
import { el } from "./util/dom";
import { sanitizeHtml } from "./util/html-sanitize";

marked.setOptions({ gfm: true, breaks: true });

export type PlanCard = {
  id: string;
  title: string;
  path: string;
  markdown: string;
  checkpoint: string;
  rolled_back: boolean;
};

export type ApprovalCard = {
  req_id: string;
  path: string;
  scope: string;
  situation: string;
  why: string;
  changes: string;
  impact: string;
  status: string;
  note: string;
};

export type TurnOpts = { latestPlan?: boolean };

const SCOPE_LABEL: Record<string, string> = {
  tools: "Python 工具",
  hooks: "钩子脚本",
  surfaces: "画面渲染",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "待确认",
  granted: "已授权",
  refused: "已拒绝",
  revised: "已记下想法",
};

const reviseDraft: Record<string, string> = {};
const reviseOpen: Record<string, boolean> = {};
const cardBodyScroll: Record<string, number> = {};

function approvalFingerprint(card: ApprovalCard, interactive: boolean): string {
  return [
    card.req_id,
    card.path,
    card.scope,
    card.status,
    card.situation,
    card.why,
    card.changes,
    card.impact,
    card.note,
    interactive ? "1" : "0",
  ].join("\u0001");
}

function planFingerprint(plan: PlanCard, active: boolean): string {
  return [
    plan.id,
    plan.path,
    plan.title,
    plan.markdown,
    plan.checkpoint,
    plan.rolled_back ? "1" : "0",
    active ? "1" : "0",
  ].join("\u0001");
}

function takeReusableCard(pool: HTMLElement[], fp: string): HTMLElement | null {
  const i = pool.findIndex((n) => n.dataset.cardFp === fp);
  if (i < 0) return null;
  return pool.splice(i, 1)[0];
}

function captureCardScrolls(root: HTMLElement): void {
  for (const n of root.querySelectorAll<HTMLElement>(".kb-plan-card-body")) {
    const id = n.closest("[data-card-id]")?.getAttribute("data-card-id") || "";
    if (id) cardBodyScroll[id] = n.scrollTop;
  }
}

function restoreCardScrolls(root: HTMLElement): void {
  const apply = (): void => {
    for (const n of root.querySelectorAll<HTMLElement>(".kb-plan-card-body")) {
      const id = n.closest("[data-card-id]")?.getAttribute("data-card-id") || "";
      const top = id ? cardBodyScroll[id] : 0;
      if (top) n.scrollTop = top;
    }
  };
  apply();
  requestAnimationFrame(apply);
}

function bindCardBodyScroll(card: HTMLElement): void {
  const id = card.dataset.cardId || "";
  const body = card.querySelector<HTMLElement>(".kb-plan-card-body");
  if (!id || !body || body.dataset.scrollBound) return;
  body.dataset.scrollBound = "1";
  body.addEventListener(
    "scroll",
    () => {
      cardBodyScroll[id] = body.scrollTop;
    },
    { passive: true },
  );
}

function stampCard(card: HTMLElement, id: string, fp: string): HTMLElement {
  card.dataset.cardId = id;
  card.dataset.cardFp = fp;
  bindCardBodyScroll(card);
  return card;
}

function adoptCard(card: HTMLElement): HTMLElement {
  bindCardBodyScroll(card);
  return card;
}

export function approvalOf(job: AgentJob): ApprovalCard | null {
  const fromResult = parseApproval(job.result?.approval);
  let fromTrace: ApprovalCard | null = null;
  for (const step of [...(job.trace || [])].reverse()) {
    if (step.kind !== "approval") continue;
    try {
      fromTrace = parseApproval(JSON.parse(step.detail || "{}"));
    } catch {
      continue;
    }
    if (fromTrace) break;
  }
  if (fromResult && fromTrace && fromResult.req_id === fromTrace.req_id) {
    return { ...fromTrace, ...fromResult };
  }
  return fromResult || fromTrace;
}

function parseApproval(raw: unknown): ApprovalCard | null {
  if (!raw || typeof raw !== "object") return null;
  const p = raw as Record<string, unknown>;
  const req = String(p.req_id || "").trim();
  if (!req) return null;
  return {
    req_id: req,
    path: String(p.path || ""),
    scope: String(p.scope || ""),
    situation: String(p.situation || ""),
    why: String(p.why || ""),
    changes: String(p.changes || ""),
    impact: String(p.impact || ""),
    status: String(p.status || "pending"),
    note: String(p.note || ""),
  };
}

export function planOf(job: AgentJob): PlanCard | null {
  const raw = job.result?.plan;
  if (raw && typeof raw === "object") {
    const p = raw as Record<string, unknown>;
    if (p.markdown || p.path) {
      return {
        id: String(p.id || ""),
        title: String(p.title || "执行计划"),
        path: String(p.path || ""),
        markdown: String(p.markdown || ""),
        checkpoint: String(p.checkpoint || ""),
        rolled_back: Boolean(p.rolled_back),
      };
    }
  }
  const mode = String(job.payload?.mode || job.result?.mode || "").toLowerCase();
  if (mode !== "plan" || job.status !== "completed") return null;
  const md = extractPlanMarkdown(job.trace || []) || String(job.result?.reply || "");
  if (!looksLikePlan(md)) return null;
  return {
    id: String(job.job_id || "").slice(0, 8),
    title: titleFromMarkdown(md),
    path: "",
    markdown: md,
    checkpoint: String(job.result?.checkpoint || ""),
    rolled_back: false,
  };
}

export function latestPlanJobId(jobs: AgentJob[]): string {
  let best = "";
  let when = "";
  for (const job of jobs) {
    if (!planOf(job)) continue;
    const t = job.created_at || job.updated_at || "";
    if (!best || t >= when) {
      best = job.job_id;
      when = t;
    }
  }
  return best;
}

export function renderAgentTurn(root: HTMLElement, job: AgentJob, replay = false, opts: TurnOpts = {}): void {
  captureCardScrolls(root);
  const reusable = [...root.querySelectorAll<HTMLElement>("[data-card-id]")];
  for (const card of reusable) card.remove();
  const openOlder = Boolean(root.querySelector(".kb-turn-older[open]"));
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
    if (payload.auto_resume) meta.push("自动续传");
    if (payload.model) meta.push(String(payload.model));
    if (payload.student_answer) meta.push(`学生作答：${String(payload.student_answer)}`);
    if (payload.textbook_hint) meta.push(`书名提示：${String(payload.textbook_hint)}`);
    if (meta.length) u.append(el("p", "muted kb-turn-mode", meta.join(" · ")));
    u.append(restoreBtn(job));
    root.append(u);
  }

  const deny = denyEarly;
  const { thinks, tools, timeline, draft } = collapseTrace(job.trace || []);
  if (job.status === "completed" || job.status === "failed" || deny) {
    for (const tool of tools) {
      if (tool.status === "running") {
        tool.status = job.status === "completed" && !deny ? "completed" : "failed";
      }
    }
  }
  if (timeline.length) {
    const act = el("div", "kb-turn-activity kb-turn-timeline");
    const keep = job.status === "running" ? 10 : 8;
    const recent = timeline.slice(-keep);
    const older = timeline.slice(0, Math.max(0, timeline.length - keep));
    if (older.length) {
      const wrap = el("details", "kb-turn-older") as HTMLDetailsElement;
      wrap.open = openOlder;
      const sum = el("summary", "");
      sum.append(el("span", "", `更早的 ${older.length} 步`));
      wrap.append(sum);
      for (const item of older) wrap.append(timelineRow(item, openTools, false));
      act.append(wrap);
    }
    recent.forEach((item, i) => {
      const current = job.status === "running" && i === recent.length - 1 && item.kind === "think";
      act.append(timelineRow(item, openTools, current));
    });
    root.append(act);
  }

  if (job.status === "queued") {
    root.append(el("p", "kb-turn-live muted", "排队…"));
  } else if (job.status === "running" && !deny) {
    appendDraft(root, draft, "is-live");
    const status = liveStatus({
      tools,
      thinks,
      draft,
      createdAt: job.created_at,
    });
    const wait = el("p", "kb-turn-live is-status");
    const line = status.elapsed ? `${status.headline} · ${status.elapsed}` : status.headline;
    wait.append(el("span", "kb-dock-ring is-inline"), el("span", "", line));
    root.append(wait);
  } else if (job.status === "cancelled" || job.status === "failed" || deny) {
    // Stopping a turn must not swallow what the agent already wrote.
    if (!String(job.result?.reply || "").trim()) appendDraft(root, draft, "is-partial");
    if (job.status === "cancelled") root.append(el("p", "kb-turn-live muted", "已停止"));
  }

  appendResult(root, job, folded, { replay, latestPlan: Boolean(opts.latestPlan), reusable });
  const approval = approvalOf(job);
  if (approval) {
    const interactive = !replay;
    const fp = approvalFingerprint(approval, interactive);
    const reused = takeReusableCard(reusable, fp);
    root.append(
      reused
        ? adoptCard(reused)
        : stampCard(renderApprovalCard(job, approval, interactive), `approval:${approval.req_id}`, fp),
    );
  }
  const errText = job.status === "cancelled" ? "" : humanizeJobError(job.error || "") || deny;
  if (errText) root.append(el("p", "kb-turn-err", errText));
  if (job.status === "completed" || job.status === "failed" || job.status === "cancelled" || deny) {
    root.append(actionBar(job, draft));
  }
  restoreCardScrolls(root);
}

function appendDraft(root: HTMLElement, draft: string, tone: string): void {
  const live = draft.trim();
  if (!live) return;
  const p = el("div", `kb-turn-assistant ${tone} kb-md`);
  p.innerHTML = chatMarkdown(live);
  enhanceCode(p);
  root.append(p);
}

function appendResult(
  root: HTMLElement,
  job: AgentJob,
  keepFolded: boolean,
  opts: { replay?: boolean; latestPlan?: boolean; reusable?: HTMLElement[] } = {},
): void {
  if (job.status !== "completed") return;
  const res = job.result;
  if (!res) return;
  const plan = planOf(job);

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
  if (plan) {
    const extra = md && md !== plan.markdown.trim() ? md : "";
    if (extra) {
      const a = el("div", "kb-turn-assistant kb-md");
      a.innerHTML = chatMarkdown(extra);
      enhanceCode(a);
      root.append(a);
    }
    const active = Boolean(opts.latestPlan) && !opts.replay;
    const fp = planFingerprint(plan, active);
    const reused = takeReusableCard(opts.reusable || [], fp);
    root.append(
      reused
        ? adoptCard(reused)
        : stampCard(renderPlanCard(job, plan, active), `plan:${plan.id || plan.path || job.job_id}`, fp),
    );
    return;
  }
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

function enhanceCode(root: HTMLElement): void {
  for (const pre of [...root.querySelectorAll("pre")]) {
    if (pre.parentElement?.classList.contains("kb-md-prewrap")) continue;
    const wrap = el("div", "kb-md-prewrap");
    const btn = el("button", "kb-md-copy", "复制") as HTMLButtonElement;
    btn.type = "button";
    btn.onclick = () => {
      void copyText(pre.textContent || "").then((ok) => {
        btn.textContent = ok ? "已复制" : "复制失败";
        window.setTimeout(() => {
          btn.textContent = "复制";
        }, 1600);
      });
    };
    pre.replaceWith(wrap);
    wrap.append(btn, pre);
  }
}

function timelineRow(item: TimelineItem, openTools: Set<string>, current: boolean): HTMLElement {
  if (item.kind === "tool") return toolRow(item.tool, openTools);
  const beat = el("div", `kb-turn-beat${current ? " is-current" : ""}`);
  beat.append(el("span", "kb-turn-thinkmark", "✦"));
  beat.append(el("p", "kb-turn-beat-text", item.text));
  return beat;
}

function toolRow(tool: CollapsedTool, openTools: Set<string>): HTMLElement {
  const d = el("details", `kb-turn-tool is-${tool.status}`) as HTMLDetailsElement;
  d.dataset.tool = tool.name;
  d.open = openTools.has(tool.name);
  const sum = el("summary", "");
  sum.append(el("span", "kb-turn-toolmark", tool.status === "running" ? "◌" : tool.status === "failed" ? "✕" : "✓"));
  sum.append(el("span", "kb-turn-tool-label", toolLabel(tool)));
  d.append(sum);
  if (tool.detail) d.append(el("pre", "kb-turn-toolbody", prettyDetail(tool.detail)));
  return d;
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

function renderApprovalCard(job: AgentJob, card: ApprovalCard, interactive: boolean): HTMLElement {
  const pending = card.status === "pending" && interactive;
  const wrap = el(
    "div",
    `kb-plan-card kb-approval-card${pending ? "" : " is-readonly"}${card.status === "refused" ? " is-refused" : ""}${card.status === "granted" ? " is-granted" : ""}`,
  );
  const head = el("header", "kb-plan-card-head");
  head.append(el("span", "kb-plan-card-icon", "✎"));
  head.append(el("strong", "kb-plan-card-title", `申请修改 ${card.path || "代码"}`));
  const status = el(
    "span",
    "kb-plan-card-status",
    `${SCOPE_LABEL[card.scope] || card.scope || "代码"} · ${STATUS_LABEL[card.status] || card.status}`,
  );
  head.append(status);
  wrap.append(head);

  const body = el("div", "kb-plan-card-body kb-approval-body");
  const sections: [string, string][] = [
    ["实际情况", card.situation],
    ["为什么要改", card.why],
    ["具体改哪些地方", card.changes],
    ["改完的影响", card.impact],
  ];
  for (const [label, text] of sections) {
    const block = el("section", "kb-approval-sec");
    block.append(el("h3", "kb-approval-h", label));
    block.append(el("p", "kb-approval-p", text || "（未写）"));
    body.append(block);
  }
  if (card.note && card.status === "revised") {
    const block = el("section", "kb-approval-sec");
    block.append(el("h3", "kb-approval-h", "你的想法"));
    block.append(el("p", "kb-approval-p", card.note));
    body.append(block);
  }
  wrap.append(body);

  if (!pending) return wrap;

  const foot = el("footer", "kb-plan-card-foot kb-approval-foot");
  const grant = el("button", "btn-primary kb-approval-grant", "授权") as HTMLButtonElement;
  const refuse = el("button", "btn kb-approval-refuse", "拒绝") as HTMLButtonElement;
  const other = el("button", "btn kb-approval-other", "其他想法") as HTMLButtonElement;
  grant.type = "button";
  refuse.type = "button";
  other.type = "button";

  const setBusy = (busy: boolean): void => {
    grant.disabled = busy;
    refuse.disabled = busy;
    other.disabled = busy;
  };

  const decide = async (choice: "grant" | "refuse" | "revise", note = ""): Promise<void> => {
    setBusy(true);
    try {
      await agent.decideApproval(card.req_id, choice, job.job_id, note);
      if (choice === "grant") {
        window.dispatchEvent(new CustomEvent("kb:continue-breakpoint"));
      } else if (choice === "revise") {
        window.dispatchEvent(new CustomEvent("kb:approval-note", { detail: { text: note } }));
      }
    } catch (e) {
      setBusy(false);
      window.alert((e as Error).message || "提交失败");
    }
  };

  grant.onclick = () => void decide("grant");
  refuse.onclick = () => void decide("refuse");

  const box = el("div", `kb-approval-revise${reviseOpen[card.req_id] ? "" : " hidden"}`);
  const area = document.createElement("textarea");
  area.className = "kb-approval-note";
  area.rows = 3;
  area.placeholder = "写下你的想法，Agent 会按这个重提申请";
  area.value = reviseDraft[card.req_id] || "";
  const send = el("button", "btn-primary kb-approval-send", "发送") as HTMLButtonElement;
  send.type = "button";
  send.disabled = !area.value.trim();
  area.addEventListener("input", () => {
    reviseDraft[card.req_id] = area.value;
    send.disabled = !area.value.trim();
  });
  send.onclick = () => {
    const note = area.value.trim();
    if (!note) return;
    void decide("revise", note);
  };
  box.append(area, send);

  other.onclick = () => {
    const open = !box.classList.contains("hidden") ? false : true;
    reviseOpen[card.req_id] = open;
    box.classList.toggle("hidden", !open);
    if (open) area.focus();
  };

  foot.append(grant, refuse, other);
  wrap.append(foot, box);
  return wrap;
}

function renderPlanCard(job: AgentJob, plan: PlanCard, active: boolean): HTMLElement {
  const card = el("div", `kb-plan-card${active ? "" : " is-readonly"}${plan.rolled_back ? " is-rolled" : ""}`);
  const head = el("header", "kb-plan-card-head");
  head.append(el("span", "kb-plan-card-icon", "☰"));
  head.append(el("strong", "kb-plan-card-title", plan.title || "执行计划"));
  if (plan.rolled_back) head.append(el("span", "kb-plan-card-status", "已回退"));
  else if (!active) head.append(el("span", "kb-plan-card-status muted", "较早计划"));
  card.append(head);
  const body = el("div", "kb-plan-card-body kb-md");
  body.innerHTML = chatMarkdown(plan.markdown || "");
  enhanceCode(body);
  card.append(body);
  if (active) {
    const foot = el("footer", "kb-plan-card-foot");
    const build = el("button", "btn-primary kb-plan-build", "执行") as HTMLButtonElement;
    build.type = "button";
    build.disabled = plan.rolled_back;
    build.onclick = () => {
      const cid = String(job.payload?.conversation_id || job.result?.conversation_id || "");
      window.dispatchEvent(
        new CustomEvent("kb:execute-plan", {
          detail: {
            plan_id: plan.id,
            path: plan.path,
            title: plan.title,
            markdown: plan.markdown,
            conversation_id: cid,
          },
        }),
      );
    };
    const rewind = el("button", "btn kb-plan-rewind", "回退") as HTMLButtonElement;
    rewind.type = "button";
    rewind.disabled = !plan.checkpoint || plan.rolled_back;
    rewind.onclick = async () => {
      if (!plan.checkpoint) return;
      rewind.disabled = true;
      try {
        const { rememberRestore } = await import("./live");
        rememberRestore(String(job.payload?.conversation_id || ""), "", {});
        const out = await agent.restoreCheckpoint(plan.checkpoint);
        rememberRestore(String(out.conversation_id || job.payload?.conversation_id || ""), String(out.message || ""), {
          changes: out.changes || [],
          removed: (out.removed_job_ids || []).length,
          kept_plan: Boolean((out as { kept_plan?: boolean }).kept_plan),
        });
        window.dispatchEvent(new CustomEvent("kb:restore-applied", { detail: out }));
      } catch (e) {
        rewind.disabled = false;
        window.alert((e as Error).message || "回退失败");
      }
    };
    foot.append(build, rewind);
    card.append(foot);
  }
  return card;
}

function checkpointOf(job: AgentJob): string {
  return String(job.result?.checkpoint || job.payload?.checkpoint || "").trim();
}

function restoreBtn(job: AgentJob): HTMLButtonElement {
  const checkpoint = checkpointOf(job);
  const btn = iconBtn("restore", "回退到这一轮", ICONS.restore);
  btn.classList.add("kb-turn-user-restore");
  btn.disabled = !checkpoint;
  btn.onclick = async () => {
    if (!checkpoint) return;
    btn.disabled = true;
    const user = String(job.payload?.message || job.payload?.problem_text || "").trim();
    const cid = String(job.payload?.conversation_id || job.result?.conversation_id || "").trim();
    try {
      const { rememberRestore } = await import("./live");
      rememberRestore(cid, user, {});
      const out = await agent.restoreCheckpoint(checkpoint);
      rememberRestore(String(out.conversation_id || cid), String(out.message || user), {
        changes: out.changes || [],
        removed: (out.removed_job_ids || []).length,
      });
      window.dispatchEvent(new CustomEvent("kb:restore-applied", { detail: out }));
    } catch (e) {
      btn.disabled = false;
      window.alert((e as Error).message || "回退失败");
    }
  };
  return btn;
}

function actionBar(job: AgentJob, draft = ""): HTMLElement {
  const bar = el("div", "kb-turn-actions");
  const reply = replyText(job, draft);
  const checkpoint = checkpointOf(job);

  const copyBtn = actionChip("copy", "复制", ICONS.copy);
  copyBtn.title = "复制这一轮回答";
  copyBtn.setAttribute("aria-label", "复制这一轮回答");
  copyBtn.disabled = !reply;
  copyBtn.onclick = async () => {
    if (!reply) return;
    const ok = await copyText(reply);
    copyBtn.classList.toggle("is-done", ok);
    copyBtn.classList.toggle("is-fail", !ok);
    copyBtn.title = ok ? "已复制" : "复制失败";
    const label = copyBtn.querySelector(".kb-turn-act-label");
    if (label) label.textContent = ok ? "已复制" : "复制失败";
    window.setTimeout(() => {
      copyBtn.classList.remove("is-done", "is-fail");
      copyBtn.title = "复制这一轮回答";
      if (label) label.textContent = "复制";
    }, 1600);
  };
  bar.append(copyBtn);

  const autoId = String((job.result || {}).auto_resume_job_id || "").trim();
  if (autoId && (job.status === "failed" || job.status === "cancelled")) {
    bar.append(el("span", "muted kb-turn-autoresume", "已自动续传一次"));
  } else if (job.status === "failed" || job.status === "cancelled") {
    const go = el("button", "btn kb-turn-continue", "从断点继续") as HTMLButtonElement;
    go.type = "button";
    go.title = "接着上一轮停住的地方做，不翻更早的对话";
    go.onclick = () => {
      window.dispatchEvent(new CustomEvent("kb:continue-breakpoint"));
    };
    bar.append(go);
  }

  const forkBtn = actionChip("fork", "分叉", ICONS.fork);
  forkBtn.title = "从这一轮开新对话（工作区也回到这一轮的文件）";
  forkBtn.setAttribute("aria-label", "从这一轮开新对话（工作区也回到这一轮的文件）");
  forkBtn.disabled = !checkpoint;
  forkBtn.onclick = async () => {
    if (!checkpoint) return;
    const { hardReload, rememberState } = await import("./live");
    forkBtn.disabled = true;
    try {
      const name = `turn-${checkpoint.slice(0, 8)}-${Date.now().toString(36)}`;
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

function iconBtn(kind: string, title: string, svg: string): HTMLButtonElement {
  const btn = el("button", `kb-turn-act kb-turn-act--${kind}`) as HTMLButtonElement;
  btn.type = "button";
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.innerHTML = svg;
  return btn;
}

function actionChip(kind: string, title: string, svg: string): HTMLButtonElement {
  const btn = el("button", `kb-turn-act kb-turn-act--chip kb-turn-act--${kind}`) as HTMLButtonElement;
  btn.type = "button";
  btn.title = title;
  btn.setAttribute("aria-label", title);
  btn.insertAdjacentHTML("afterbegin", svg);
  btn.append(el("span", "kb-turn-act-label", title));
  return btn;
}

const ICONS = {
  copy: `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><rect x="5.5" y="5.5" width="8" height="8" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><rect x="2.5" y="2.5" width="8" height="8" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>`,
  restore: `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3.2 8A4.8 4.8 0 1 0 4.6 4.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M2.2 2.8v3.1h3.1" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
  fork: `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="4" cy="3.2" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="4" cy="12.8" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="12" cy="8" r="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M4 4.8v7M4 8h6.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>`,
};

function examplesOf(raw: unknown): string {
  if (!Array.isArray(raw) || !raw.length) return "";
  return raw.map(String).join("、");
}

export function humanizeJobError(raw: string): string {
  const t = (raw || "").trim();
  if (!t) return "";
  if (/桥接超时|没有回包|任务超时|发送超时|权限拒绝|本地桥接失败|桥接认证|上一轮 Cursor 任务还在跑|残留任务/.test(t)) return t;
  const low = t.toLowerCase();
  if (low.includes("detached running") || low.includes("no run event store")) {
    return "上一轮残留任务无法接到前台，已失效";
  }
  if (low.includes("already has active run") || low.includes("agent_busy")) {
    return "上一轮 Cursor 任务还在跑，接到前台失败，请再点一次";
  }
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
