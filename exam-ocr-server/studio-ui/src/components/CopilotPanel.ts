// Buddy Copilot 对话面板（Cursor SDK 编排服务前端）。
//
// 内嵌在中栏（不再弹出对话框），整体仿照 Cursor 的 Agent 对话界面：
//   - 消息流：用户气泡 / assistant 正文（markdown 渲染 + 长文折叠 + 复制）
//     / 工具步骤行（可展开看参数与结果）/ 计划卡 / 确认卡（支持「本会话总是允许」）
//     / 任务清单卡（实时打勾）/ 文件改动 diff 卡 / 最终报告卡
//   - 实时性：SSE（Server-Sent Events）事件流推送；不可用时自动回退 2s 轮询
//   - 输入区：圆角组合框（Enter 发送 / Shift+Enter 换行 / 粘贴图片作为附件）
//     + 模型下拉 + 历史会话入口
//
// 两个入口：
//   场景 A：导入表单「AI 方案」（kind=import_advice）→ 出现在导入表单下方
//   场景 B：页卡片「AI 助手」（kind=page_assist）→ 出现在页卡片下方

import {
  copilot,
  type CopilotImportEvent,
  type CopilotKind,
  type CopilotMessage,
  type CopilotPlan,
  type CopilotPlanStep,
  type CopilotSessionSummary,
} from "../api/copilot";
import {
  clearActiveMinerUJob,
  focusMinerUJobTriPane,
  persistActiveMinerUJob,
} from "./import/activeMinerUJob";
import { store } from "../state/store";
import { el, renderMarkdown } from "../util/dom";

/** 打开导入工作台并定位到任务预览（MinerU / 35B 分线）。 */
function focusImportJob(jobId: string, opts?: { line?: string; pageNo?: number }): void {
  const id = String(jobId || "").trim();
  if (!id) return;
  const line = (opts?.line || "mineru").toLowerCase();
  const pageNo = opts?.pageNo && opts.pageNo > 0 ? opts.pageNo : undefined;
  store.focusImportWorkspace();
  store.showImportPreview();
  if (line === "35b" || line === "b35") {
    // 与 B35ImportTree / ImportPipelinePanel 事件名对齐
    document.dispatchEvent(new CustomEvent("studio:import-line-b35"));
    document.dispatchEvent(
      new CustomEvent("studio:b35-navigate", {
        detail: { jobId: id, pageNo },
      }),
    );
  } else {
    focusMinerUJobTriPane(id, pageNo);
  }
}

function importCtaButton(label: string, onClick: () => void): HTMLButtonElement {
  const btn = el("button", "copilot-import-cta", label) as HTMLButtonElement;
  btn.type = "button";
  btn.onclick = onClick;
  return btn;
}

const STATUS_LABEL: Record<string, string> = {
  planning: "拟定行动简报",
  awaiting_user: "等待你补充信息",
  awaiting_approval: "等待确认（可继续对话）",
  executing: "自主执行中",
  verifying: "验收中",
  done: "已完成",
  failed: "已失败",
  cancelled: "已取消",
};

const RUNNING_STATUSES = new Set(["planning", "executing", "verifying"]);
const TERMINAL_STATUSES = new Set(["done", "failed", "cancelled"]);

const RISK_LABEL: Record<string, string> = { low: "低风险", medium: "中风险", high: "高风险" };

const REPORT_STATUS_LABEL: Record<string, string> = {
  done: "任务完成",
  blocked: "需要你的输入",
  failed: "任务失败",
};

const CHANNEL_LABEL: Record<string, string> = {
  readonly: "只读",
  cheap: "便宜通道",
  expensive: "昂贵通道",
};

const KIND_LABEL: Record<string, string> = {
  import_advice: "AI 方案建议",
  page_assist: "页面分诊",
};

const TODO_ICON: Record<string, string> = {
  done: "✓",
  in_progress: "◔",
  pending: "○",
  skipped: "⊘",
};

const MODEL_STORAGE_KEY = "copilot.model";
/** assistant 气泡超过该高度（px）自动折叠。 */
const FOLD_PX = 360;

function fmtErr(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e ?? "unknown");
  return raw.length > 400 ? `${raw.slice(0, 400)}…` : raw;
}

function fmtTime(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** assistant 文本里的报告 JSON 块以卡片呈现，正文里折叠掉。 */
function stripPlanJson(text: string): string {
  return text.replace(/```json\s*\{[\s\S]*?\}\s*```/g, "").trim();
}

function stepLine(s: CopilotPlanStep): HTMLElement {
  const row = el("div", "copilot-plan-step");
  const ch = el(
    "span",
    `copilot-chan copilot-chan--${s.channel}`,
    CHANNEL_LABEL[s.channel] || s.channel,
  );
  row.append(
    el("span", "copilot-plan-step-id", s.id),
    el("span", "copilot-plan-step-title", s.title),
    el("code", "copilot-plan-step-tool", s.tool),
    ch,
  );
  return row;
}

/** diff 补丁文本 → 按行着色的 DOM。 */
function renderDiffPre(patch: string): HTMLElement {
  const pre = el("pre", "copilot-diff");
  for (const line of patch.split("\n")) {
    const ln = el("div", "copilot-diff-line");
    if (line.startsWith("+") && !line.startsWith("+++")) ln.classList.add("copilot-diff-add");
    else if (line.startsWith("-") && !line.startsWith("---")) ln.classList.add("copilot-diff-del");
    else if (line.startsWith("@@")) ln.classList.add("copilot-diff-hunk");
    else if (line.startsWith("diff ") || line.startsWith("+++") || line.startsWith("---"))
      ln.classList.add("copilot-diff-meta");
    ln.textContent = line || " ";
    pre.appendChild(ln);
  }
  return pre;
}

type PanelCleanup = () => void;
const HOST_CLEANUP = new WeakMap<HTMLElement, PanelCleanup>();
/** 宿主 → 绑定的导入 job（可遍历；WeakMap 无 entries） */
const HOST_JOB_BIND = new Map<HTMLElement, string>();

/** 程序化输入通道：宿主面板（导入面板等）把事件作为用户消息注入活动会话。 */
const HOST_SEND = new WeakMap<HTMLElement, (text: string) => void>();

/** 指定挂载点上是否有活动的 Copilot 面板。 */
export function hasActiveCopilot(host: HTMLElement): boolean {
  return HOST_SEND.has(host);
}

/** 关掉挂载点上的面板（不删服务端会话）。新导入开新对话前调用。 */
export function closeCopilotPanel(host: HTMLElement): void {
  HOST_CLEANUP.get(host)?.();
  host.replaceChildren();
}

/** 向挂载点上的活动面板注入一条用户消息（如「PDF 已预上传，minio_key=…」）。
 *  无活动面板返回 false；会话尚未就绪时会短暂等待后发送。 */
export function copilotSendText(host: HTMLElement, text: string): boolean {
  const fn = HOST_SEND.get(host);
  if (!fn) return false;
  fn(text);
  return true;
}

const SESSION_STORAGE_PREFIX = "studio.copilot.session.";

function sessionStorageKey(kind: CopilotKind, jobId = ""): string {
  const jid = String(jobId || "").trim();
  return jid
    ? `${SESSION_STORAGE_PREFIX}${kind}.${jid}`
    : `${SESSION_STORAGE_PREFIX}${kind}`;
}

function persistSessionId(
  kind: CopilotKind,
  sid: string,
  jobId = "",
  opts: { markActive?: boolean } = {},
): void {
  try {
    const jid = String(jobId || "").trim();
    window.localStorage.setItem(sessionStorageKey(kind, jid), sid);
    if (jid) {
      // 历史回放时不改「当前活跃任务」指针，避免把三栏拽回旧任务
      if (opts.markActive !== false) persistActiveMinerUJob(jid);
      window.localStorage.setItem(sessionStorageKey(kind), sid);
    } else {
      window.localStorage.setItem(sessionStorageKey(kind), sid);
    }
  } catch {
    /* ignore */
  }
}

function clearPersistedSessionId(
  kind: CopilotKind,
  jobId = "",
  opts: { keepActiveJob?: boolean } = {},
): void {
  try {
    const jid = String(jobId || "").trim();
    if (jid) {
      window.localStorage.removeItem(sessionStorageKey(kind, jid));
      // 只删对话时保留「活跃任务」指针，中栏预览不该跟着消失
      if (!opts.keepActiveJob) clearActiveMinerUJob(jid);
      window.localStorage.removeItem(sessionStorageKey(kind));
    } else {
      window.localStorage.removeItem(sessionStorageKey(kind));
    }
  } catch {
    /* ignore */
  }
}

/** 导入任务删除后：清本地绑定并关掉展示该任务对话的面板。 */
export function onImportJobDeleted(jobId: string): void {
  const jid = String(jobId || "").trim();
  if (!jid) return;
  clearPersistedSessionId("import_advice", jid);
  clearActiveMinerUJob(jid);
  for (const [host, bound] of [...HOST_JOB_BIND.entries()]) {
    if (bound !== jid) continue;
    HOST_CLEANUP.get(host)?.();
    HOST_JOB_BIND.delete(host);
    host.removeAttribute("data-copilot-job");
    host.replaceChildren();
  }
  // 网关已级联删服务端会话；再调一次幂等兜底
  void copilot.deleteByImportJob(jid).catch(() => undefined);
}

/** 刷新后恢复右栏 Copilot：优先按 import job 绑定恢复。 */
export async function tryRestoreCopilotPanel(opts: {
  kind: CopilotKind;
  host: HTMLElement;
  title?: string;
  /** 当前预览/选中的导入任务；与 AI 会话 1:1 */
  jobId?: string;
  /** 切换任务时强制换绑（关掉宿主上其它 job 的面板） */
  force?: boolean;
}): Promise<boolean> {
  const wantJob = String(opts.jobId || "").trim();
  const bound = opts.host.getAttribute("data-copilot-job") || "";
  if (hasActiveCopilot(opts.host)) {
    if (!opts.force && (!wantJob || bound === wantJob)) return true;
    // 换任务：先拆掉旧面板（不删服务端会话）
    HOST_CLEANUP.get(opts.host)?.();
  }
  let sid = "";
  try {
    if (wantJob) {
      sid = window.localStorage.getItem(sessionStorageKey(opts.kind, wantJob)) || "";
      if (!sid) {
        const found = await copilot.findByImportJob(wantJob);
        if (found?.id) sid = found.id;
      }
    } else {
      const { loadActiveMinerUJob } = await import("./import/activeMinerUJob");
      const activeJob = loadActiveMinerUJob();
      if (activeJob) {
        sid =
          window.localStorage.getItem(sessionStorageKey(opts.kind, activeJob)) || "";
      }
      if (!sid) {
        sid = window.localStorage.getItem(sessionStorageKey(opts.kind)) || "";
      }
    }
  } catch {
    sid = "";
  }
  if (!sid) return false;
  try {
    const s = await copilot.getSession(sid);
    if (!s?.id || String(s.kind || "") !== opts.kind) {
      clearPersistedSessionId(opts.kind, wantJob);
      return false;
    }
    const payload =
      s.payload && typeof s.payload === "object"
        ? (s.payload as Record<string, unknown>)
        : {};
    const boundJob = String(
      s.import_job_id || payload.import_job_id || payload.job_id || "",
    ).trim();
    // 指定了 job 时必须匹配，避免串台
    if (wantJob && boundJob && boundJob !== wantJob) {
      clearPersistedSessionId(opts.kind, wantJob);
      return false;
    }
    openCopilotPanel({
      kind: opts.kind,
      host: opts.host,
      payload: { ...payload, ...(wantJob ? { import_job_id: wantJob, job_id: wantJob } : {}) },
      resumeSessionId: sid,
      title: opts.title,
    });
    return true;
  } catch {
    clearPersistedSessionId(opts.kind, wantJob);
    return false;
  }
}

export function openCopilotPanel(opts: {
  kind: CopilotKind;
  payload: Record<string, unknown>;
  /** 面板挂载点（中栏内嵌，不弹层）。 */
  host: HTMLElement;
  title?: string;
  /** 刷新恢复：挂到已有会话，不新建。 */
  resumeSessionId?: string;
}): void {
  // 同一个挂载点只保留一个面板
  HOST_CLEANUP.get(opts.host)?.();
  // 清掉空状态文案，避免与对话面板叠在一起
  opts.host.replaceChildren();

  const panel = el("div", "copilot-inline");

  // ---- 头部 ----
  const head = el("div", "copilot-inline-head");
  const titleWrap = el("div", "copilot-inline-title");
  titleWrap.append(
    el("span", "copilot-spark", "✦"),
    el(
      "span",
      "",
      opts.title || (opts.kind === "import_advice" ? "AI 方案建议" : "AI 助手 · 页面分诊"),
    ),
  );
  const statusPill = el("span", "copilot-status", "连接中…");
  const historyBtn = el("button", "copilot-icon-btn", "🕘") as HTMLButtonElement;
  historyBtn.type = "button";
  historyBtn.title = "AI 对话列表（进入 / 删除）";
  const closeBtn = el("button", "copilot-icon-btn", "✕") as HTMLButtonElement;
  closeBtn.type = "button";
  closeBtn.title = "关闭面板（会话在后台保留）";
  head.append(titleWrap, statusPill, historyBtn, closeBtn);

  // ---- 历史会话下拉 ----
  const historyPanel = el("div", "copilot-history hidden");

  // ---- 消息流 ----
  const stream = el("div", "copilot-stream");
  // 常驻运行状态行（shimmer），始终吸底
  const liveLine = el("div", "copilot-live hidden");
  const liveDot = el("span", "copilot-live-dot", "");
  const liveText = el("span", "copilot-live-text", "");
  liveLine.append(liveDot, liveText);

  // ---- 输入区（Cursor 风格组合框） ----
  const composer = el("div", "copilot-composer");
  const attachRow = el("div", "copilot-attach-row hidden");
  const textarea = document.createElement("textarea");
  textarea.className = "copilot-composer-input";
  textarea.rows = 1;
  textarea.placeholder =
    opts.kind === "page_assist"
      ? "继续对话或描述哪里不满意（Enter 发送，可粘贴截图）"
      : "继续对话、追问下一步，或明确说「拒绝/改方案」（Enter 发送）";
  const composerFoot = el("div", "copilot-composer-foot");

  const modelSelect = document.createElement("select");
  modelSelect.className = "copilot-model-select";
  modelSelect.title = "会话使用的模型；「自动」由服务端选择。会话创建时生效";
  const autoOpt = document.createElement("option");
  autoOpt.value = "";
  autoOpt.textContent = "自动";
  modelSelect.appendChild(autoOpt);
  const savedModel = window.localStorage.getItem(MODEL_STORAGE_KEY) || "";
  void copilot
    .listModels()
    .then(({ models }) => {
      for (const m of models) {
        const o = document.createElement("option");
        o.value = m.id;
        o.textContent = m.label || m.id;
        modelSelect.appendChild(o);
      }
      if (savedModel && [...modelSelect.options].some((o) => o.value === savedModel)) {
        modelSelect.value = savedModel;
      }
    })
    .catch(() => {
      /* 列表拉不到时只剩「自动」，不阻塞会话 */
    });

  const attachBtn = el("button", "copilot-icon-btn", "📎") as HTMLButtonElement;
  attachBtn.type = "button";
  attachBtn.title = "添加图片附件（也可直接粘贴到输入框）";
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/*";
  fileInput.multiple = true;
  fileInput.className = "hidden";

  const stopBtn = el("button", "copilot-stop-btn hidden", "⏹ 终止") as HTMLButtonElement;
  stopBtn.type = "button";
  stopBtn.title = "终止当前会话";
  const sendBtn = el("button", "copilot-send-btn", "↑") as HTMLButtonElement;
  sendBtn.type = "button";
  sendBtn.title = "发送（Enter；Shift+Enter 换行）";

  composerFoot.append(
    modelSelect,
    attachBtn,
    fileInput,
    el("span", "copilot-composer-space"),
    stopBtn,
    sendBtn,
  );
  composer.append(attachRow, textarea, composerFoot);

  panel.append(head, historyPanel, stream, liveLine, composer);
  opts.host.appendChild(panel);
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });

  // ---- 会话状态 ----
  let sid = "";
  let afterSeq = 0;
  /** 首批（历史消息回放）只渲染，不触发导航 / 轮询等副作用 —— 否则刷新会
   *  把上百条历史 import_event 当实时事件重放，瞬间拉起大量预览轮询把页面卡死。 */
  let hydrating = true;
  let pollTimer: number | null = null;
  let eventSource: EventSource | null = null;
  let closed = false;
  /** 待发送附件（已上传，发送时把路径带进消息文本）。 */
  const pendingAttachments: { path: string; filename: string }[] = [];
  /** 任务清单卡（原地更新）。 */
  let todosCard: HTMLElement | null = null;
  /** 批准卡登记表：approval_id → 卡片控制（供 approval_resolved 同步状态）。 */
  const approvalCards = new Map<string, { card: HTMLElement; settle: (status: string) => void }>();
  /** 最近一个 assistant 气泡（增量合并用）。 */
  let lastAssistant: { wrap: HTMLElement; body: HTMLElement; raw: string } | null = null;

  const scrollDown = () => {
    stream.scrollTop = stream.scrollHeight;
  };

  const setLive = (status: string): void => {
    const label = STATUS_LABEL[status] || status;
    statusPill.textContent = label;
    statusPill.className = `copilot-status copilot-status--${status}`;
    if (RUNNING_STATUSES.has(status)) {
      liveText.textContent = `${label}…`;
      liveLine.classList.remove("hidden");
    } else {
      liveLine.classList.add("hidden");
    }
    const running = !TERMINAL_STATUSES.has(status) && status !== "";
    stopBtn.classList.toggle("hidden", !running);
  };

  /** 折叠检查：超高的 assistant 气泡收起 + 展开按钮。 */
  const applyFold = (wrap: HTMLElement, body: HTMLElement): void => {
    const existing = wrap.querySelector(":scope > .copilot-fold-btn") as HTMLButtonElement | null;
    if (body.scrollHeight <= FOLD_PX) {
      existing?.remove();
      body.classList.remove("copilot-foldable", "copilot-folded");
      return;
    }
    body.classList.add("copilot-foldable");
    if (!existing) {
      body.classList.add("copilot-folded");
      const btn = el("button", "copilot-fold-btn", "展开全文 ▾") as HTMLButtonElement;
      btn.type = "button";
      btn.onclick = () => {
        const folded = body.classList.toggle("copilot-folded");
        btn.textContent = folded ? "展开全文 ▾" : "收起 ▴";
      };
      wrap.appendChild(btn);
    }
  };

  /** 复制按钮（悬浮在气泡右上角）。 */
  const addCopyBtn = (wrap: HTMLElement, getText: () => string): void => {
    const btn = el("button", "copilot-copy-btn", "⧉") as HTMLButtonElement;
    btn.type = "button";
    btn.title = "复制";
    btn.onclick = () => {
      void navigator.clipboard
        .writeText(getText())
        .then(() => {
          btn.textContent = "✓";
          window.setTimeout(() => (btn.textContent = "⧉"), 1200);
        })
        .catch(() => {
          btn.textContent = "✗";
          window.setTimeout(() => (btn.textContent = "⧉"), 1200);
        });
    };
    wrap.appendChild(btn);
  };

  const addBubble = (
    cls: string,
    text: string,
    opts?: { actions?: { id?: string; label: string; send: string }[] },
  ): void => {
    if (!text && !opts?.actions?.length) return;
    if (cls === "copilot-msg--assistant") {
      // 连续 assistant 增量合并进同一个气泡（后端按段缓冲落库）
      if (lastAssistant && stream.lastElementChild === lastAssistant.wrap) {
        lastAssistant.raw += text;
        lastAssistant.body.innerHTML = renderMarkdown(stripPlanJson(lastAssistant.raw));
        applyFold(lastAssistant.wrap, lastAssistant.body);
        scrollDown();
        return;
      }
      const wrap = el("div", "copilot-msg copilot-msg--assistant");
      const body = el("div", "copilot-msg-body copilot-md");
      const raw = text;
      body.innerHTML = renderMarkdown(stripPlanJson(raw));
      wrap.appendChild(body);
      lastAssistant = { wrap, body, raw };
      addCopyBtn(wrap, () => stripPlanJson(lastAssistant?.raw ?? ""));
      stream.appendChild(wrap);
      applyFold(wrap, body);
      scrollDown();
      return;
    }
    lastAssistant = null;
    const b = el("div", `copilot-msg ${cls}`);
    if (text) {
      const body = el("div", "copilot-msg-body");
      body.style.whiteSpace = "pre-wrap";
      body.textContent = text;
      b.appendChild(body);
    }
    if (cls === "copilot-msg--user" && text) addCopyBtn(b, () => text);
    const actions = opts?.actions || [];
    if (actions.length) {
      const row = el("div", "copilot-msg-actions");
      for (const a of actions) {
        const label = String(a.label || a.send || "继续");
        const sendText = String(a.send || label);
        const btn = el("button", "copilot-continue-btn", label) as HTMLButtonElement;
        btn.type = "button";
        btn.onclick = () => {
          if (!sid) return;
          const draft = textarea.value;
          textarea.value = sendText;
          void send().finally(() => {
            if (!textarea.value) textarea.value = draft;
          });
        };
        row.appendChild(btn);
      }
      b.appendChild(row);
    }
    stream.appendChild(b);
    scrollDown();
  };

  const renderPlanCard = (plan: CopilotPlan): void => {
    lastAssistant = null;
    const card = el("div", "copilot-card copilot-plan-card");
    card.append(
      el("div", "copilot-card-title", `计划 · ${plan.intent}`),
      el("div", "copilot-plan-summary", plan.summary),
      el(
        "div",
        `copilot-risk copilot-risk--${plan.risk}`,
        `风险 ${plan.risk}${plan.risk_note ? ` · ${plan.risk_note}` : ""}`,
      ),
    );
    if (plan.steps.length) {
      card.append(el("div", "copilot-plan-label", "执行步骤"));
      for (const s of plan.steps) card.append(stepLine(s));
    }
    if (plan.verification?.length) {
      card.append(el("div", "copilot-plan-label", "验收步骤"));
      for (const s of plan.verification) card.append(stepLine(s));
    }
    if (plan.user_options) {
      card.append(el("div", "copilot-plan-options muted", `待你决策：${plan.user_options}`));
    }
    stream.appendChild(card);
    scrollDown();
  };

  /** 批准卡：标题/说明/风险由后端元数据下发；闸门工具多一个「总是允许」。 */
  const renderApprovalCard = (content: any): void => {
    lastAssistant = null;
    const card = el("div", "copilot-card copilot-approval-card");
    const isBrief =
      content.action_key === "brief_confirm" || content.action_key === "plan_confirm";
    const title = String(content.title || content.action_key || "需要确认");
    card.append(el("div", "copilot-card-title", isBrief ? "是否按上方简报执行？" : title));
    if (!isBrief && content.action_key) {
      card.append(el("code", "copilot-tool-name", String(content.action_key)));
    }
    if (content.description) {
      card.append(el("div", "copilot-plan-summary", String(content.description)));
    }
    if (content.risk) {
      card.append(
        el(
          "div",
          `copilot-risk copilot-risk--${content.risk}`,
          RISK_LABEL[String(content.risk)] || String(content.risk),
        ),
      );
    }
    if (content.args && Object.keys(content.args).length) {
      const pre = el("pre", "copilot-report");
      pre.textContent = JSON.stringify(content.args, null, 2);
      card.append(el("div", "copilot-plan-label", "参数"), pre);
    }
    const btns = el("div", "copilot-approval-btns");
    const ok = el("button", "btn-primary", isBrief ? "确认执行" : "批准") as HTMLButtonElement;
    const always = isBrief
      ? null
      : (el("button", "btn-link", "批准 · 本会话总是允许") as HTMLButtonElement);
    const no = el("button", "btn-link", "拒绝") as HTMLButtonElement;
    ok.type = "button";
    no.type = "button";
    const allBtns = [ok, no, ...(always ? [always] : [])];
    const decide = async (approved: boolean, remember = false) => {
      for (const b of allBtns) b.disabled = true;
      const note = approved ? "" : textarea.value.trim();
      if (!approved && note) textarea.value = "";
      try {
        await copilot.decide(sid, String(content.approval_id), approved, note, remember);
        card.classList.add(approved ? "copilot-approved" : "copilot-rejected");
      } catch (e) {
        addBubble("copilot-msg--system", `确认提交失败：${fmtErr(e)}`);
        for (const b of allBtns) b.disabled = false;
      }
    };
    ok.onclick = () => void decide(true);
    no.onclick = () => void decide(false);
    no.title = "拒绝时可先在下方输入框写明原因，会作为反馈带给 Agent";
    btns.append(ok);
    if (always) {
      always.type = "button";
      always.title = "本会话内同一动作不再询问（不跨会话）";
      always.onclick = () => void decide(true, true);
      btns.append(always);
    }
    btns.append(no);
    card.append(btns);
    if (isBrief) {
      card.append(
        el("div", "copilot-plan-options muted", "也可以直接在下方输入框发消息，作为修改意见让 Agent 重拟简报。"),
      );
    }
    // 登记：后端 approval_resolved 消息（用户消息替代 / 超时 / 他端决定）同步卡片状态
    const aid = String(content.approval_id || "");
    if (aid) {
      approvalCards.set(aid, {
        card,
        settle: (status: string) => {
          for (const b of allBtns) b.disabled = true;
          if (status === "approved") card.classList.add("copilot-approved");
          else card.classList.add("copilot-rejected");
          if (status === "superseded") {
            card.append(el("div", "copilot-plan-options muted", "已由你的消息替代，Agent 正在按你的意见处理。"));
          } else if (status === "expired") {
            card.append(el("div", "copilot-plan-options muted", "等待超时，已按拒绝处理。"));
          }
        },
      });
    }
    stream.appendChild(card);
    scrollDown();
  };

  /** approval_resolved：把已出结果的批准卡置灰/标注（消息替代、超时、取消等）。 */
  const resolveApproval = (content: any): void => {
    const entry = approvalCards.get(String(content.approval_id || ""));
    if (entry) entry.settle(String(content.status || "rejected"));
  };

  /** 工具步骤行：done/error 行可展开查看参数与结果。 */
  const renderStep = (content: any): void => {
    const st = String(content.state || "");
    const title = String(content.title ?? content.step?.title ?? "");
    const tool = String(content.tool ?? content.step?.tool ?? "");
    if (st === "start") {
      // start 行会被后续 done/error 行取代，仅在运行状态行提示
      liveText.textContent = `执行工具 ${title || tool}…`;
      liveLine.classList.remove("hidden");
      return;
    }
    lastAssistant = null;
    const icon = st === "done" ? "✓" : st === "error" ? "✗" : st === "skipped" ? "⊘" : "◌";
    const cls =
      st === "done" ? "done" : st === "error" ? "error" : st === "skipped" ? "skipped" : "run";

    const details = document.createElement("details");
    details.className = `copilot-tool copilot-tool--${cls}`;
    const summary = document.createElement("summary");
    summary.className = "copilot-tool-summary";
    summary.append(
      el("span", "copilot-tool-icon", icon),
      el("span", "copilot-tool-title", title),
      el("code", "copilot-tool-name", tool),
    );
    if (typeof content.elapsed_ms === "number") {
      const sec = content.elapsed_ms / 1000;
      summary.append(
        el(
          "span",
          "copilot-tool-elapsed muted",
          sec >= 1 ? `${sec.toFixed(1)}s` : `${content.elapsed_ms}ms`,
        ),
      );
    }
    if (content.error) summary.append(el("span", "copilot-tool-err", String(content.error)));
    if (st === "skipped" && content.note) {
      summary.append(el("span", "copilot-tool-err", `已拒绝：${String(content.note)}`));
    }
    details.appendChild(summary);

    const body = el("div", "copilot-tool-body");
    if (content.args && Object.keys(content.args).length) {
      const pre = el("pre", "copilot-report");
      pre.textContent = JSON.stringify(content.args, null, 2);
      body.append(el("div", "copilot-plan-label", "参数"), pre);
    }
    if (typeof content.result === "string" && content.result) {
      const pre = el("pre", "copilot-report");
      pre.textContent = content.result;
      body.append(el("div", "copilot-plan-label", "结果摘要"), pre);
    }
    if (body.childElementCount) {
      details.appendChild(body);
    } else {
      details.classList.add("copilot-tool--empty");
    }
    stream.appendChild(details);
    scrollDown();
  };

  /** 任务清单卡：原地更新（Cursor Todo 风格）。 */
  const renderTodos = (content: any): void => {
    const items: any[] = Array.isArray(content.items) ? content.items : [];
    if (!todosCard) {
      todosCard = el("div", "copilot-card copilot-todos-card");
      stream.appendChild(todosCard);
    }
    todosCard.replaceChildren();
    const doneCount = items.filter((it) => it.status === "done").length;
    todosCard.append(
      el("div", "copilot-card-title", `任务清单 ${doneCount}/${items.length}`),
    );
    for (const it of items) {
      const status = String(it.status || "pending");
      const row = el("div", `copilot-todo copilot-todo--${status}`);
      row.append(
        el("span", "copilot-todo-icon", TODO_ICON[status] || "○"),
        el("span", "copilot-todo-text", String(it.text || "")),
      );
      todosCard.append(row);
    }
    scrollDown();
  };

  /** 文件改动 diff 卡。 */
  const renderDiff = (content: any): void => {
    lastAssistant = null;
    const files: string[] = Array.isArray(content.files) ? content.files.map(String) : [];
    const card = el("div", "copilot-card copilot-diff-card");
    card.append(el("div", "copilot-card-title", `文件改动 · ${files.length} 个文件`));
    for (const f of files) card.append(el("code", "copilot-diff-file", f));
    if (content.stat) {
      const pre = el("pre", "copilot-report");
      pre.textContent = String(content.stat).trim();
      card.append(pre);
    }
    if (content.patch) {
      const details = document.createElement("details");
      details.className = "copilot-diff-details";
      const summary = document.createElement("summary");
      summary.textContent = "查看补丁";
      details.append(summary, renderDiffPre(String(content.patch)));
      card.append(details);
    }
    stream.appendChild(card);
    scrollDown();
  };

  let lastImportJobId = "";
  let lastImportLine = "mineru";

  const renderImportEvent = (c: CopilotImportEvent, live = true): void => {
    lastAssistant = null;
    const jobId = String(c.job_id || "").trim();
    const line = String(c.line || "mineru");
    const ev = String(c.event || "");
    const mineru = line !== "35b" && line !== "b35";
    if (jobId) {
      lastImportJobId = jobId;
      lastImportLine = line;
      if (sid) persistSessionId(opts.kind, sid, jobId, { markActive: live });
      HOST_JOB_BIND.set(opts.host, jobId);
      opts.host.setAttribute("data-copilot-job", jobId);
    }

    // 历史回放（刷新后重放会话）只渲染卡片：不导航、不起轮询、不刷左树
    if (!live) {
      // 仅补齐卡片，下面继续走渲染
    } else if (
      jobId &&
      (ev === "import_started" || ev === "import_done" || ev === "import_failed")
    ) {
      // 任务级事件才切中栏（低频）
      focusImportJob(jobId, { line, pageNo: c.page_no });
      if (mineru) {
        persistActiveMinerUJob(jobId);
        void import("../services/importProgress").then(({ startImportProgressPolling }) => {
          startImportProgressPolling(jobId);
        });
        void import("./import/MinerUImportTree").then(({ emitMinerUTreeRefresh }) => {
          emitMinerUTreeRefresh(jobId);
        });
      }
    } else if (jobId && ev === "reprocess_done" && mineru) {
      // 逐页重处理很密集：只刷左树，不抢中栏视图（否则每页拉起一轮滚动/加载循环）
      void import("./import/MinerUImportTree").then(
        ({ scheduleMinerUTreeRefresh, emitMinerUPageSoftRefresh }) => {
          scheduleMinerUTreeRefresh(jobId);
          // Cursor IR / 单页写盘：中栏对该页做无闪烁原地刷新（不整栏 remount）
          const pn = Number(c.page_no) || 0;
          const mode = String(c.mode || "");
          if (pn > 0 && (mode === "cursor_ir" || mode === "rerender" || !mode)) {
            emitMinerUPageSoftRefresh(jobId, pn);
          }
        },
      );
    } else if (jobId && ev === "import_progress") {
      const ready = c.pages_ready != null ? ` · 已出稿 ${c.pages_ready}` : "";
      const phase = c.pipeline_phase ? ` · ${c.pipeline_phase}` : "";
      setLive(`导入中 ${c.status || ""}${ready}${phase}`);
      if (mineru) {
        // 漏掉 import_started 时，首条进度也打开中栏，避免中间空白
        if (store.importCenterView !== "preview") {
          persistActiveMinerUJob(jobId);
          focusImportJob(jobId, { line, pageNo: c.page_no });
        }
        void import("./import/MinerUImportTree").then(({ scheduleMinerUTreeRefresh }) => {
          scheduleMinerUTreeRefresh(jobId);
        });
      }
    }

    if (ev === "import_progress") {
      // 进度不另开卡片（system 也不刷屏）；live 行已更新
      return;
    }

    const titles: Record<string, string> = {
      import_started: "已发起导入",
      import_done: "导入完成",
      import_failed: "导入未完成",
      reprocess_done: `页 ${c.page_no ?? "?"} 已重处理`,
    };
    const card = el(
      "div",
      `copilot-card copilot-import-card ${
        ev === "import_failed" ? "copilot-result-bad" : "copilot-result-ok"
      }`,
    );
    card.append(el("div", "copilot-card-title", titles[ev] || ev));
    const meta: string[] = [];
    if (jobId) meta.push(`job_id=${jobId}`);
    if (c.page_limit) meta.push(`预览版前 ${c.page_limit} 页`);
    else if (ev === "import_started" || ev === "import_done") meta.push("预览版导入");
    if (c.status) meta.push(`状态 ${c.status}`);
    if (c.pages_ready != null) meta.push(`pages_ready=${c.pages_ready}`);
    if (c.edition_id) meta.push(`方案 ${c.edition_id}`);
    if (c.error) meta.push(String(c.error));
    if (meta.length) card.append(el("div", "copilot-plan-summary muted", meta.join(" · ")));
    if (jobId) {
      card.append(
        importCtaButton("在导入面板查看", () =>
          focusImportJob(jobId, { line, pageNo: c.page_no }),
        ),
      );
    }
    stream.appendChild(card);
    scrollDown();
  };

  /** 最终报告卡（FinalReport JSON），兼容旧字符串 report。 */
  const renderResult = (c: any): void => {
    lastAssistant = null;
    const r = c.report;
    const card = el(
      "div",
      `copilot-card ${c.ok ? "copilot-result-ok" : "copilot-result-bad"}`,
    );
    if (r && typeof r === "object") {
      const st = String(r.status || (c.ok ? "done" : "failed"));
      card.append(el("div", "copilot-card-title", REPORT_STATUS_LABEL[st] || st));
      if (r.summary) {
        const body = el("div", "copilot-plan-summary copilot-md");
        body.innerHTML = renderMarkdown(String(r.summary));
        card.append(body);
      }
      const jobId = String(r.job_id || lastImportJobId || "").trim();
      const line = String(r.import_line || lastImportLine || "mineru");
      if (jobId) {
        lastImportJobId = jobId;
        lastImportLine = line;
        card.append(
          el(
            "div",
            "copilot-plan-summary",
            `已关联导入任务 job_id=${jobId}` +
              (String(r.summary || "").includes("导入") ? "" : "（预览版）"),
          ),
        );
        card.append(
          importCtaButton("在导入面板查看任务", () => focusImportJob(jobId, { line })),
        );
      }
      const ev: unknown[] = Array.isArray(r.evidence) ? r.evidence : [];
      if (ev.length) {
        card.append(el("div", "copilot-plan-label", "验证证据"));
        for (const e of ev) card.append(el("div", "copilot-plan-step", `· ${String(e)}`));
      }
      const plan: any[] = Array.isArray(r.page_plan) ? r.page_plan : [];
      if (plan.length) {
        card.append(el("div", "copilot-plan-label", "页级排版方案"));
        const table = el("div", "copilot-pageplan");
        const header = el("div", "copilot-pageplan-row copilot-pageplan-head");
        for (const h of ["页码", "版式", "方案", "处理路径", "备注"]) {
          header.append(el("span", "", h));
        }
        table.append(header);
        for (const p of plan) {
          const row = el("div", "copilot-pageplan-row");
          row.append(
            el("span", "copilot-pageplan-pages", String(p.pages ?? "")),
            el("span", "", String(p.category ?? "")),
            el("span", "copilot-pageplan-edition", String(p.edition_id ?? "")),
            el("span", "", String(p.action ?? "")),
            el("span", "muted", String(p.notes ?? "")),
          );
          table.append(row);
        }
        card.append(table);
      }
      if (r.question) {
        card.append(el("div", "copilot-plan-options", `待你回答：${String(r.question)}`));
      }
      if (r.next_steps) {
        card.append(el("div", "copilot-plan-options muted", `建议：${String(r.next_steps)}`));
      }
    } else {
      card.append(
        el("div", "copilot-card-title", c.ok ? "验收通过" : "验收未通过"),
        el("pre", "copilot-report", String(r ?? "")),
      );
      if (lastImportJobId) {
        card.append(
          importCtaButton("在导入面板查看任务", () =>
            focusImportJob(lastImportJobId, { line: lastImportLine }),
          ),
        );
      }
    }
    stream.appendChild(card);
    scrollDown();
  };

  const renderMessage = (m: CopilotMessage, live = true): void => {
    const c = m.content || {};
    switch (m.role) {
      case "user":
        addBubble("copilot-msg--user", String(c.text ?? ""));
        break;
      case "assistant":
        addBubble("copilot-msg--assistant", String(c.text ?? ""));
        break;
      case "system": {
        const actions = Array.isArray(c.actions)
          ? (c.actions as { id?: string; label: string; send: string }[])
          : undefined;
        addBubble("copilot-msg--system", String(c.text ?? ""), { actions });
        break;
      }
      case "status":
        setLive(String(c.status ?? ""));
        break;
      case "plan":
        renderPlanCard(c as CopilotPlan);
        break;
      case "approval":
        renderApprovalCard(c);
        break;
      case "approval_resolved":
        resolveApproval(c);
        break;
      case "step":
        renderStep(c);
        break;
      case "todos":
        renderTodos(c);
        break;
      case "diff":
        renderDiff(c);
        break;
      case "verify":
        renderStep({
          step: c.step,
          state: c.ok ? "done" : "error",
          error: c.ok ? "" : "验收未通过",
        });
        break;
      case "result":
        renderResult(c);
        break;
      case "import_event":
        renderImportEvent(c as CopilotImportEvent, live);
        break;
      default:
        break;
    }
  };

  const applyBatch = (status: string, messages: CopilotMessage[]): void => {
    const live = !hydrating;
    for (const m of messages) {
      afterSeq = Math.max(afterSeq, m.seq);
      renderMessage(m, live);
    }
    hydrating = false;
    setLive(status);
  };

  // ---- 实时通道：SSE 优先，失败回退轮询 ----

  const stopChannels = (): void => {
    if (pollTimer != null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    eventSource?.close();
    eventSource = null;
  };

  const tick = async (): Promise<void> => {
    if (!sid || closed) return;
    try {
      const out = await copilot.getMessages(sid, afterSeq);
      applyBatch(out.status, out.messages);
      if (TERMINAL_STATUSES.has(out.status) && pollTimer != null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    } catch (e) {
      statusPill.textContent = `轮询失败：${fmtErr(e)}`;
    }
  };

  const startPolling = (): void => {
    if (pollTimer != null || closed) return;
    pollTimer = window.setInterval(() => void tick(), 2000);
    void tick();
  };

  const startStream = (): void => {
    if (closed || !sid) return;
    stopChannels();
    if (typeof window.EventSource === "undefined") {
      startPolling();
      return;
    }
    const es = new EventSource(copilot.eventsUrl(sid, afterSeq));
    eventSource = es;
    es.addEventListener("batch", (ev) => {
      try {
        const data = JSON.parse((ev as MessageEvent).data);
        applyBatch(String(data.status || ""), data.messages || []);
      } catch {
        /* 单条解析失败不断流 */
      }
    });
    es.addEventListener("end", () => {
      es.close();
      if (eventSource === es) eventSource = null;
    });
    es.onerror = () => {
      // SSE 断开（网关重启/代理不支持流式）→ 回退轮询
      es.close();
      if (eventSource === es) {
        eventSource = null;
        startPolling();
      }
    };
  };

  /** 挂到某个会话（新建后或从历史选择后调用）。 */
  const attachSession = (targetSid: string, resetStream: boolean): void => {
    stopChannels();
    sid = targetSid;
    afterSeq = 0;
    hydrating = true;
    const jobFromPayload = String(
      opts.payload?.import_job_id || opts.payload?.job_id || lastImportJobId || "",
    ).trim();
    persistSessionId(opts.kind, targetSid, jobFromPayload);
    if (jobFromPayload) {
      HOST_JOB_BIND.set(opts.host, jobFromPayload);
      opts.host.setAttribute("data-copilot-job", jobFromPayload);
    }
    if (resetStream) {
      stream.replaceChildren();
      todosCard = null;
      lastAssistant = null;
      approvalCards.clear();
    }
    startStream();
  };

  // ---- AI 对话管理（切换 / 删除；进入即三栏对齐） ----
  let historyOpen = false;

  /** 进入某条对话：右栏挂接 + 左栏任务高亮 + 中栏该任务预览。 */
  const enterSession = (s: CopilotSessionSummary): void => {
    const jid = String(s.import_job_id || "").trim();
    const kind = (s.kind as CopilotKind) || opts.kind;
    if (jid) {
      // 先落绑定，避免中栏预览再触发一次「恢复会话」把面板重建
      HOST_JOB_BIND.set(opts.host, jid);
      opts.host.setAttribute("data-copilot-job", jid);
      persistSessionId(kind, s.id, jid);
    }
    if (s.id !== sid) attachSession(s.id, true);
    if (jid) focusImportJob(jid, { line: "mineru" });
  };

  const removeSession = async (s: CopilotSessionSummary): Promise<void> => {
    const name = s.label || s.id.slice(0, 8);
    if (!confirm(`删除 AI 对话「${name}」？\n（只删对话，导入任务与已出稿的页不受影响）`)) {
      return;
    }
    try {
      await copilot.deleteSession(s.id);
    } catch (e) {
      alert(`删除失败：${fmtErr(e)}`);
      return;
    }
    clearPersistedSessionId((s.kind as CopilotKind) || opts.kind, s.import_job_id || "", {
      keepActiveJob: true,
    });
    if (s.id === sid) {
      // 删的是当前这条：关掉面板，回到空状态
      cleanup();
      return;
    }
    await renderHistory();
  };

  const renderHistory = async (): Promise<void> => {
    historyPanel.replaceChildren(el("div", "muted", "加载中…"));
    try {
      const { sessions } = await copilot.listSessions();
      historyPanel.replaceChildren();
      historyPanel.append(
        el("div", "copilot-history-hint muted", "点一条进入：右栏对话 · 中栏预览 · 左栏任务同步"),
      );
      if (!sessions.length) {
        historyPanel.append(el("div", "muted", "暂无 AI 对话"));
        return;
      }
      for (const s of sessions.slice(0, 30)) {
        const row = el("div", "copilot-history-row");
        if (s.id === sid) row.classList.add("copilot-history-current");
        const openBtn = el("button", "copilot-history-open") as HTMLButtonElement;
        openBtn.type = "button";
        openBtn.title = s.import_job_id
          ? `进入对话并打开任务 ${s.import_job_id}`
          : "进入对话（未绑定导入任务）";
        openBtn.append(
          el(
            "span",
            `copilot-status copilot-status--${s.status}`,
            STATUS_LABEL[s.status] || s.status,
          ),
          el("span", "copilot-history-label", s.label || KIND_LABEL[s.kind] || s.kind),
          el("span", "copilot-history-time muted", fmtTime(s.created_at)),
        );
        openBtn.onclick = () => {
          historyOpen = false;
          historyPanel.classList.add("hidden");
          enterSession(s);
        };
        const delBtn = el("button", "copilot-history-del", "删除") as HTMLButtonElement;
        delBtn.type = "button";
        delBtn.title = "删除这条 AI 对话（不动导入任务）";
        delBtn.onclick = (e) => {
          e.stopPropagation();
          void removeSession(s);
        };
        row.append(openBtn, delBtn);
        historyPanel.append(row);
      }
    } catch (e) {
      historyPanel.replaceChildren(el("div", "muted", `列表加载失败：${fmtErr(e)}`));
    }
  };

  const toggleHistory = async (): Promise<void> => {
    historyOpen = !historyOpen;
    historyPanel.classList.toggle("hidden", !historyOpen);
    if (!historyOpen) return;
    await renderHistory();
  };
  historyBtn.onclick = () => void toggleHistory();

  // ---- 附件 ----
  const renderAttachRow = (): void => {
    attachRow.replaceChildren();
    attachRow.classList.toggle("hidden", pendingAttachments.length === 0);
    pendingAttachments.forEach((att, i) => {
      const chip = el("span", "copilot-attach-chip");
      chip.append(el("span", "", `🖼 ${att.filename}`));
      const rm = el("button", "copilot-icon-btn", "✕") as HTMLButtonElement;
      rm.type = "button";
      rm.onclick = () => {
        pendingAttachments.splice(i, 1);
        renderAttachRow();
      };
      chip.append(rm);
      attachRow.append(chip);
    });
  };

  const uploadFiles = async (files: File[]): Promise<void> => {
    if (!sid) {
      addBubble("copilot-msg--system", "会话尚未就绪，稍后再试附件。");
      return;
    }
    for (const f of files) {
      if (!f.type.startsWith("image/")) continue;
      try {
        const out = await copilot.uploadAttachment(sid, f);
        pendingAttachments.push({ path: out.path, filename: out.filename });
      } catch (e) {
        addBubble("copilot-msg--system", `附件上传失败：${fmtErr(e)}`);
      }
    }
    renderAttachRow();
  };

  attachBtn.onclick = () => fileInput.click();
  fileInput.onchange = () => {
    void uploadFiles([...(fileInput.files || [])]);
    fileInput.value = "";
  };
  textarea.addEventListener("paste", (ev) => {
    const items = ev.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const it of items) {
      if (it.kind === "file" && it.type.startsWith("image/")) {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      ev.preventDefault();
      void uploadFiles(files);
    }
  });

  // ---- 控制 ----
  const cleanup: PanelCleanup = () => {
    closed = true;
    stopChannels();
    panel.remove();
    HOST_CLEANUP.delete(opts.host);
    HOST_JOB_BIND.delete(opts.host);
    HOST_SEND.delete(opts.host);
    opts.host.removeAttribute("data-copilot-job");
  };
  HOST_CLEANUP.set(opts.host, cleanup);
  {
    const bootJob = String(opts.payload?.import_job_id || opts.payload?.job_id || "").trim();
    if (bootJob) {
      HOST_JOB_BIND.set(opts.host, bootJob);
      opts.host.setAttribute("data-copilot-job", bootJob);
    }
  }
  closeBtn.onclick = cleanup;

  stopBtn.onclick = async () => {
    if (!sid) return;
    stopBtn.disabled = true;
    try {
      await copilot.cancel(sid);
    } catch {
      /* 会话可能已结束 */
    } finally {
      stopBtn.disabled = false;
    }
  };

  modelSelect.onchange = () => {
    window.localStorage.setItem(MODEL_STORAGE_KEY, modelSelect.value);
    if (sid) {
      addBubble("copilot-msg--system", "模型选择已保存，将在下一个会话生效（当前会话继续用原模型）。");
    }
  };

  const send = async (): Promise<void> => {
    let text = textarea.value.trim();
    if ((!text && pendingAttachments.length === 0) || !sid) return;
    if (pendingAttachments.length) {
      const lines = pendingAttachments.map((a) => `（附件图片，请查看：${a.path}）`);
      text = [text, ...lines].filter(Boolean).join("\n");
    }
    sendBtn.disabled = true;
    try {
      await copilot.sendMessage(sid, text);
      textarea.value = "";
      pendingAttachments.length = 0;
      renderAttachRow();
      autoGrow();
      // 终态续聊：SSE 已随会话结束关闭，发送成功后重开实时通道
      if (!eventSource && pollTimer == null) startStream();
    } catch (e) {
      addBubble("copilot-msg--system", `发送失败：${fmtErr(e)}`);
    } finally {
      sendBtn.disabled = false;
    }
  };
  sendBtn.onclick = () => void send();
  // 程序化注入（宿主面板调用）：会话未就绪时每 500ms 重试，最多 20 次
  HOST_SEND.set(opts.host, (text: string) => {
    const attempt = (left: number): void => {
      if (closed) return;
      if (!sid) {
        if (left > 0) window.setTimeout(() => attempt(left - 1), 500);
        return;
      }
      const draft = textarea.value; // 保住用户正在输入的草稿
      textarea.value = text;
      void send().finally(() => {
        if (!textarea.value) textarea.value = draft;
      });
    };
    attempt(20);
  });
  textarea.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    if (ev.shiftKey) return; // Shift+Enter 换行
    if (ev.isComposing) return; // 输入法组词中
    ev.preventDefault();
    void send();
  });
  const autoGrow = (): void => {
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 140)}px`;
  };
  textarea.addEventListener("input", autoGrow);

  // ---- 启动 / 恢复会话 ----
  const resumeId = String(opts.resumeSessionId || "").trim();
  if (resumeId) {
    setLive("running");
    addBubble("copilot-msg--system", "正在恢复与本导入任务绑定的 AI 会话…");
    attachSession(resumeId, false);
  } else {
    setLive("planning");
    addBubble("copilot-msg--system", "正在创建 Copilot 会话…");
    void (async () => {
      try {
        const bootJob = String(
          opts.payload?.import_job_id || opts.payload?.job_id || "",
        ).trim();
        // 已有 job：优先挂已绑定会话，避免一任务多对话
        if (bootJob && opts.kind === "import_advice") {
          const found = await copilot.findByImportJob(bootJob);
          if (found?.id) {
            addBubble(
              "copilot-msg--system",
              `已挂接本导入任务的既有 AI 对话（job=${bootJob.slice(0, 8)}…）。`,
            );
            attachSession(found.id, false);
            return;
          }
        }
        const model = window.localStorage.getItem(MODEL_STORAGE_KEY) || "";
        const out = await copilot.createSession(opts.kind, opts.payload, model);
        if (out.resumed) {
          addBubble("copilot-msg--system", "已复用与本导入任务绑定的 AI 对话。");
        }
        attachSession(out.session_id, false);
      } catch (e) {
        addBubble(
          "copilot-msg--system",
          `会话创建失败：${fmtErr(e)}\n请确认宿主机已运行 buddy-copilot/run.sh 且已配置 CURSOR_API_KEY。`,
        );
        setLive("failed");
        statusPill.textContent = "未连接";
      }
    })();
  }
}
