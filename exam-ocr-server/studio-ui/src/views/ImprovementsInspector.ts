/**
 * 改进点右栏：本书概览 · AI 对话 · 当前选中详情 / 拆分·聚合工作台。
 * 全书入口：整理 / 全部拆分 / 处理全部聚合；单条入口：拆分本条（行内或详情）。
 */
import type { CopilotKind, IpBookIntent } from "../api/copilot";
import {
  corpus,
  type CheckDryRunResult,
  type CheckExamplesResult,
  type ChecksOverview,
  type SymptomCheckInfo,
} from "../api/corpus";
import {
  improvementPoints,
  LANDING_TARGETS,
  normalizePrepStatus,
  patterns,
  patternStatusLabel,
  PREP_STATUS_LABELS,
  type ConflictPair,
  type ImprovementPattern,
  type ImprovementPoint,
  type IpLineageEvent,
  type IpListOut,
  type PatternPromotionCheck,
  type PrepStatus,
  type SplitProposal,
} from "../api/improvementPoints";
import { focusMinerUJobTriPane } from "../components/import/activeMinerUJob";
import { store } from "../state/store";
import {
  baseName,
  checkBySourceFile,
  derivedUsedIn,
  findCheck,
  glossaryRow,
  LAYER_LABEL,
  namesUsedIn,
  SCOPE_LABEL,
  vocabWord,
} from "../util/detectorVocab";
import { el } from "../util/dom";

type IpContext = {
  jobId: string;
  fingerprintHash: string;
  jobLabel: string;
  selectedIds: string[];
  counts: IpListOut["counts"] | null;
  items: ImprovementPoint[];
};

type WorkbenchMode = null | "split" | "conflict";

let bodyEl: HTMLElement | null = null;
/** 右栏 AI 对话挂载点（与导入 Copilot 同组件，内嵌不弹层）。 */
let copilotHost: HTMLElement | null = null;
let ctx: IpContext = {
  jobId: "",
  fingerprintHash: "",
  jobLabel: "",
  selectedIds: [],
  counts: null,
  items: [],
};
/** 中栏「改进点 | 抽象结果 | 检测器」切换 */
let viewMode: "ips" | "patterns" | "detectors" | "vocab" = "ips";
let focusPattern: ImprovementPattern | null = null;
/** 检测器页签选中的症状名；点词时 highlightWord 用来滚到该词释义。 */
let focusSuspect = "";
let highlightWord = "";
/** 词表页签选中的挂载层词。 */
let focusVocabWord = "";
let promoBusy = false;
let promoResult: PatternPromotionCheck | null = null;
let promoError = "";
/** 加载器现状（生效中的检测器 + 词表 + 没能加载的声明），点开模式时才取。 */
let checksOverview: ChecksOverview | null = null;
let checksBusy = false;
let checksError = "";
/** 检测器自检结果，跟着选中的模式走，换一条就清空。 */
let examplesBusy = false;
let examplesResult: CheckExamplesResult | null = null;
let examplesError = "";
let dryBusy = false;
let dryResult: CheckDryRunResult | null = null;
let dryError = "";

const SEVERITY_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

function resetDetectorSelfTest(): void {
  examplesResult = null;
  examplesError = "";
  dryResult = null;
  dryError = "";
}

const IP_FOCUS_KEY = "studio.improvements.focus.v1";

export type PersistedIpFocus = {
  jobId: string;
  fingerprintHash: string;
  jobLabel: string;
};

const IP_BOOK_TITLE = "AI · 本书改进点";

function intentUserText(intent: IpBookIntent, payload: Record<string, unknown>): string {
  const ipId = String(payload.ip_id || "").trim();
  if (intent === "aggregate") {
    const members = Array.isArray(payload.member_ids)
      ? (payload.member_ids as unknown[])
          .map((x) => String(x || "").trim())
          .filter(Boolean)
      : [];
    const label = String(
      payload.aggregate_cluster_label ||
        (payload.aggregate_cluster_index
          ? `簇#${payload.aggregate_cluster_index}`
          : ""),
    ).trim();
    if (members.length && !payload.process_all) {
      return (
        `【意图：处理聚合 · 仅${label || "指定簇"}】` +
        `member_ids=${JSON.stringify(members)}。` +
        "只裁决这一簇；禁止其它簇；不要问全部还是一个。"
      );
    }
    return "【意图：处理全部聚合】请按簇#序号处理全书对立簇，说明 keep/delete，确认后再写库。";
  }
  if (intent === "split_all") {
    return "【意图：全部拆分】请清空该书全部 needs_split，逐条 propose→apply。";
  }
  if (intent === "split_one") {
    return `【意图：拆分本条】ip_id=${ipId || "（缺失）"}。请提案并确认后写库。`;
  }
  return "【意图：整理】请清空该书全部 needs_triage，直到 remaining_needs_triage=0。";
}

export function loadPersistedIpFocus(): PersistedIpFocus | null {
  try {
    const raw = window.localStorage.getItem(IP_FOCUS_KEY);
    if (!raw) return null;
    const o = JSON.parse(raw) as Partial<PersistedIpFocus>;
    return {
      jobId: String(o.jobId || "").trim(),
      fingerprintHash: String(o.fingerprintHash || "").trim(),
      jobLabel: String(o.jobLabel || "").trim(),
    };
  } catch {
    return null;
  }
}

export function persistIpFocus(focus: PersistedIpFocus): void {
  try {
    window.localStorage.setItem(
      IP_FOCUS_KEY,
      JSON.stringify({
        jobId: String(focus.jobId || "").trim(),
        fingerprintHash: String(focus.fingerprintHash || "").trim(),
        jobLabel: String(focus.jobLabel || "").trim(),
      }),
    );
  } catch {
    /* ignore */
  }
}

/* ip-book-v1 */
/** 关掉改进点右栏 AI（不删服务端会话）。「全部」/换无会话的书时用。 */
async function clearImprovementsCopilot(): Promise<void> {
  if (!copilotHost) return;
  const { closeCopilotPanel } = await import("../components/CopilotPanel");
  closeCopilotPanel(copilotHost);
  copilotHost.classList.remove("is-active");
  copilotHost.removeAttribute("data-copilot-scope");
  copilotHost.removeAttribute("data-copilot-job");
  copilotHost.replaceChildren();
}

/**
 * 按书指纹 1:1 同步右栏 AI。
 * - 无指纹（「全部」）→ 关闭对话
 * - 有指纹 → 只挂该书 ip_book 会话；该书无会话则保持空
 */
async function syncImprovementsCopilot(
  fingerprintHash: string,
  jobId = "",
): Promise<void> {
  if (!copilotHost) return;
  const fp = String(fingerprintHash || "").trim();
  if (!fp) {
    await clearImprovementsCopilot();
    return;
  }
  const { tryRestoreCopilotPanel, hasActiveCopilot, closeCopilotPanel } =
    await import("../components/CopilotPanel");
  const bound = (
    copilotHost.getAttribute("data-copilot-scope") ||
    copilotHost.getAttribute("data-copilot-job") ||
    ""
  ).trim();
  // 已绑同指纹且面板真正存活（有发送通道）→ 仅确保可见
  if (
    hasActiveCopilot(copilotHost) &&
    bound === fp &&
    copilotHost.querySelector(".copilot-inline")
  ) {
    copilotHost.classList.add("is-active");
    return;
  }
  // 换书 / 陈旧空壳：拆掉再按指纹恢复（合并脚本删会话后 localStorage 常失效）
  closeCopilotPanel(copilotHost);
  copilotHost.classList.remove("is-active");

  const jid = String(jobId || ctx.jobId || "").trim();
  const ok = await tryRestoreCopilotPanel({
    kind: "ip_book",
    host: copilotHost,
    scopeKey: fp,
    jobId: jid || undefined,
    createIfMissing: false,
    force: true,
    title: IP_BOOK_TITLE,
  });
  if (ok) {
    const nowBound = (
      copilotHost.getAttribute("data-copilot-scope") || ""
    ).trim();
    if (nowBound && nowBound !== fp) {
      await clearImprovementsCopilot();
      return;
    }
    copilotHost.classList.add("is-active");
    copilotHost.setAttribute("data-copilot-scope", fp);
    return;
  }
  await clearImprovementsCopilot();
}

/** 从旧 kind / payload 推断一书对话意图。 */
function resolveIpIntent(
  kind: CopilotKind | undefined,
  payload: Record<string, unknown>,
  explicit?: IpBookIntent,
): IpBookIntent {
  if (explicit) return explicit;
  const raw = String(payload.intent || "").trim().toLowerCase();
  if (
    raw === "triage" ||
    raw === "split_all" ||
    raw === "split_one" ||
    raw === "aggregate"
  ) {
    return raw;
  }
  if (String(payload.focus || "").toLowerCase() === "aggregate") {
    return "aggregate";
  }
  if (kind === "ip_split") {
    return payload.process_all ? "split_all" : "split_one";
  }
  if (payload.ip_id && !payload.process_all) return "split_one";
  return "triage";
}

/**
 * 打开本书改进点 AI（一书一会话）。
 * 四入口只改 intent；宿主已绑同一指纹时不新建面板，只复用并开跑。
 */
export async function openImprovementsCopilot(opts: {
  /** @deprecated 一律归一为 ip_book；保留仅为兼容旧调用 */
  kind?: CopilotKind;
  intent?: IpBookIntent;
  payload: Record<string, unknown>;
  title?: string;
}): Promise<void> {
  if (!copilotHost) {
    window.alert("改进点 AI 面板未就绪，请刷新页面后重试。");
    return;
  }
  const fp = String(
    opts.payload?.fingerprint_hash || ctx.fingerprintHash || "",
  ).trim();
  if (!fp) {
    window.alert("请先在左侧点选一本书，再打开 AI 整理（需按书指纹绑定对话）。");
    return;
  }
  const intent = resolveIpIntent(opts.kind, opts.payload, opts.intent);
  const payload: Record<string, unknown> = {
    ...opts.payload,
    fingerprint_hash: fp,
    intent,
  };
  const memberIds = Array.isArray(payload.member_ids)
    ? (payload.member_ids as unknown[])
        .map((x) => String(x || "").trim())
        .filter(Boolean)
    : [];
  /** 中栏点簇：前端亲自发送带「簇#」的用户文案，避免只靠后端 kick 漏标签 */
  const scopedAggregate =
    intent === "aggregate" && memberIds.length > 0 && !payload.process_all;
  const kickText = intentUserText(intent, payload);
  persistIpFocus({
    jobId: String(payload.job_id || payload.import_job_id || ctx.jobId || "").trim(),
    fingerprintHash: fp,
    jobLabel: String(payload.label || ctx.jobLabel || "").trim(),
  });
  const {
    openCopilotPanel,
    closeCopilotPanel,
    hasActiveCopilot,
    copilotSendText,
    copilotResync,
  } = await import("../components/CopilotPanel");
  const { copilot } = await import("../api/copilot");
  const bound = (copilotHost.getAttribute("data-copilot-scope") || "").trim();
  if (bound && bound !== fp) {
    closeCopilotPanel(copilotHost);
  }

  const kickSession = async (resumeSessionId?: string): Promise<string> => {
    const model = window.localStorage.getItem("copilot.model") || "";
    const out = await copilot.createSession("ip_book", payload, model, {
      // 单簇：先落 payload，再 sendMessage 带簇标签；全书仍 auto_start
      auto_start: !scopedAggregate,
    });
    const sid = String(out.session_id || resumeSessionId || "").trim();
    if (scopedAggregate && sid) {
      await copilot.sendMessage(sid, kickText);
    }
    return sid;
  };

  // 已绑同一指纹且面板存活：复用会话开跑，并重接消息流（避免 SSE 终态断开后漏画）
  if (hasActiveCopilot(copilotHost) && bound === fp) {
    try {
      const sid = await kickSession();
      if (!copilotResync(copilotHost)) {
        openCopilotPanel({
          kind: "ip_book",
          host: copilotHost,
          payload,
          title: opts.title || IP_BOOK_TITLE,
          resumeSessionId: sid,
        });
      }
    } catch (e) {
      if (!copilotSendText(copilotHost, kickText)) {
        window.alert(`启动本书改进点对话失败：${e}`);
        return;
      }
      copilotResync(copilotHost);
    }
    copilotHost.classList.add("is-active");
    copilotHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
    return;
  }

  // 无面板：先查已有会话并 resume，保证立刻画出历史（含刚跑完的整理/拆分）
  let resumeId = "";
  try {
    const found = await copilot.findByFingerprint(fp, "ip_book");
    if (found?.id) resumeId = found.id;
  } catch {
    resumeId = "";
  }
  if (resumeId) {
    openCopilotPanel({
      kind: "ip_book",
      host: copilotHost,
      payload,
      title: opts.title || IP_BOOK_TITLE,
      resumeSessionId: resumeId,
    });
    try {
      await kickSession(resumeId);
      copilotResync(copilotHost);
    } catch {
      /* 仅展示历史也可 */
    }
  } else {
    openCopilotPanel({
      kind: "ip_book",
      host: copilotHost,
      payload,
      title: opts.title || IP_BOOK_TITLE,
    });
  }
  copilotHost.setAttribute("data-copilot-scope", fp);
  copilotHost.classList.add("is-active");
  copilotHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/**
 * 打开「抽象成模式」对话（kind=pattern_abstract）。
 * 与一书对话共用宿主与指纹 scope，但会话独立：抽象产出的是跨书检测器，
 * 不写这本书的台账。
 */
export async function openPatternAbstractCopilot(
  payload: Record<string, unknown>,
): Promise<void> {
  if (!copilotHost) {
    window.alert("改进点 AI 面板未就绪，请刷新页面后重试。");
    return;
  }
  const fp = String(payload.fingerprint_hash || ctx.fingerprintHash || "").trim();
  if (!fp) {
    window.alert("请先在左侧点选一本书，再抽象模式（需按书指纹绑定对话）。");
    return;
  }
  const body: Record<string, unknown> = { ...payload, fingerprint_hash: fp };
  const { openCopilotPanel, closeCopilotPanel } = await import(
    "../components/CopilotPanel"
  );
  const { copilot } = await import("../api/copilot");
  closeCopilotPanel(copilotHost);
  const model = window.localStorage.getItem("copilot.model") || "";
  const out = await copilot.createSession("pattern_abstract", body, model, {
    auto_start: true,
  });
  openCopilotPanel({
    kind: "pattern_abstract",
    host: copilotHost,
    payload: body,
    title: "AI · 抽象成模式",
    resumeSessionId: String(out.session_id || ""),
  });
  copilotHost.setAttribute("data-copilot-scope", fp);
  copilotHost.classList.add("is-active");
  copilotHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
let focusItem: ImprovementPoint | null = null;
let workbench: WorkbenchMode = null;
let splitDraft: SplitProposal[] = [];
/** 提案侧闸：未分配 after / 需 Agent 时禁用确认 */
let splitUnassignedAfter: number[] = [];
let splitNeedsAgent = false;
let conflictPairs: ConflictPair[] = [];
/** pairKey -> keep_a | keep_b | delete_both */
let conflictChoices = new Map<string, "keep_a" | "keep_b" | "delete_both">();
let workbenchBusy = false;
let workbenchError = "";

function prepOf(it: ImprovementPoint): PrepStatus {
  return normalizePrepStatus(it.prep_status, it.needs_split);
}

function prepLabel(it: ImprovementPoint): string {
  return (
    String(it.prep_status_label || "").trim() ||
    PREP_STATUS_LABELS[prepOf(it)] ||
    "待整理"
  );
}

function claimSummary(it: ImprovementPoint): string {
  const claim = (it.claim || {}) as Record<string, unknown>;
  const headline = String(it.headline || "").trim();
  if (headline) return headline;
  const ui = String(it.user_input || claim.user_input || "").trim();
  if (ui) {
    const ask = ui
      .split("\n")
      .map((l) => l.trim())
      .filter(
        (l) =>
          l &&
          !l.startsWith("【聚焦目标】") &&
          !l.startsWith("line=") &&
          !l.startsWith("job_id=") &&
          !l.startsWith("page_no=") &&
          !l.startsWith("block_id=") &&
          !l.startsWith("请基于该页") &&
          !l.startsWith("改动生效后") &&
          !l.includes("你无需代用户决定"),
      )
      .join(" ");
    const one = (ask || ui).replace(/\s+/g, " ").trim();
    return one.length > 80 ? `${one.slice(0, 80)}…` : one;
  }
  return String(claim.summary || it.locus_key || it.id || "").trim();
}

function para(parent: HTMLElement, text: string, cls = ""): void {
  if (!text) return;
  parent.appendChild(el("p", cls, text));
}

function preBlock(parent: HTMLElement, text: string): void {
  if (!text) return;
  const pre = el("pre", "improvements-insp-pre", text);
  parent.appendChild(pre);
}

function refreshEvent(): void {
  document.dispatchEvent(new CustomEvent("studio:improvements-refresh"));
}

function pairKey(p: ConflictPair): string {
  return [p.a_id, p.b_id].sort().join("|");
}

function closeWorkbench(): void {
  workbench = null;
  splitDraft = [];
  splitUnassignedAfter = [];
  splitNeedsAgent = false;
  conflictPairs = [];
  conflictChoices.clear();
  workbenchError = "";
  workbenchBusy = false;
  paint();
}

let headTitleEl: HTMLElement | null = null;
let headHintEl: HTMLElement | null = null;

function syncInspectorHead(): void {
  if (headTitleEl) {
    headTitleEl.textContent =
      viewMode === "vocab"
        ? "词表"
        : viewMode === "detectors"
          ? "检测器"
          : viewMode === "patterns"
            ? "抽象结果"
            : "改进点";
  }
  if (headHintEl) {
    headHintEl.textContent =
      viewMode === "vocab"
        ? "点选中栏一个配置词 / 词插件 / 派生特征，看谁在用它。"
        : viewMode === "detectors"
          ? "点选中栏一条检测器，看它用到的配置词 / 词插件与判据。"
          : viewMode === "patterns"
            ? "点选中栏模式看四元组与手册；晋级检查只出提议、不写规则。"
            : "流程：①整理 → ②全部拆分或拆分本条 → ③处理全部聚合（或中栏点簇）→ ④完成体。";
  }
}

export function mountImprovementsInspector(root: HTMLElement): void {
  root.classList.add("improvements-inspector", "edition-ops-inspector");
  root.innerHTML = "";
  const head = el("div", "improvements-insp-head");
  headTitleEl = el("h3", "inspector-title", "改进点");
  headHintEl = el(
    "p",
    "muted edition-ops-inspector-hint",
    "流程：①整理 → ②全部拆分或拆分本条 → ③处理全部聚合（或中栏点簇）→ ④完成体。",
  );
  head.appendChild(headTitleEl);
  head.appendChild(headHintEl);
  root.appendChild(head);
  copilotHost = el("div", "improvements-copilot-host");
  root.appendChild(copilotHost);
  bodyEl = el("div", "improvements-inspector-body edition-ops-inspector-body");
  root.appendChild(bodyEl);
  paint();

  // 刷新后：仅当上次选中了具体书（有指纹）才恢复；「全部」不挂对话
  const bootFocus = loadPersistedIpFocus();
  if (bootFocus?.fingerprintHash) {
    ctx = {
      ...ctx,
      jobId: bootFocus.jobId,
      fingerprintHash: bootFocus.fingerprintHash,
      jobLabel: bootFocus.jobLabel || ctx.jobLabel,
    };
    void syncImprovementsCopilot(bootFocus.fingerprintHash, bootFocus.jobId);
  } else {
    void clearImprovementsCopilot();
  }

  document.addEventListener(
    "studio:improvements-context",
    ((e: CustomEvent<Partial<IpContext>>) => {
      const d = e.detail || {};
      const prevFp = ctx.fingerprintHash;
      ctx = {
        jobId: String(d.jobId || ""),
        fingerprintHash: String(d.fingerprintHash || ""),
        jobLabel: String(d.jobLabel || ""),
        selectedIds: Array.isArray(d.selectedIds) ? d.selectedIds.map(String) : [],
        counts: d.counts || null,
        items: Array.isArray(d.items) ? d.items : [],
      };
      if (focusItem) {
        const id = String(focusItem.id || "");
        focusItem = ctx.items.find((x) => String(x.id) === id) || null;
      }
      paint();
      if (ctx.fingerprintHash !== prevFp) {
        persistIpFocus({
          jobId: ctx.jobId,
          fingerprintHash: ctx.fingerprintHash,
          jobLabel: ctx.jobLabel,
        });
        void syncImprovementsCopilot(ctx.fingerprintHash, ctx.jobId);
      }
    }) as EventListener,
  );

  document.addEventListener(
    "studio:improvements-focus",
    ((e: CustomEvent) => {
      const fp = String(e.detail?.fingerprintHash || "").trim();
      const jobId = String(e.detail?.jobId || "").trim();
      const jobLabel = String(e.detail?.jobLabel || "").trim();
      ctx = { ...ctx, jobId, fingerprintHash: fp, jobLabel };
      persistIpFocus({ jobId, fingerprintHash: fp, jobLabel });
      // 「全部」fp 为空 → 关掉对话；换书 → 只挂该书
      void syncImprovementsCopilot(fp, jobId);
    }) as EventListener,
  );

  document.addEventListener(
    "studio:improvements-select",
    ((e: CustomEvent<{ item?: ImprovementPoint | null }>) => {
      focusItem = e.detail?.item || null;
      if (workbench === "split" && focusItem && prepOf(focusItem) !== "needs_split") {
        workbench = null;
        splitDraft = [];
      }
      paint();
    }) as EventListener,
  );

  document.addEventListener(
    "studio:improvements-workbench",
    ((e: CustomEvent<{ mode?: WorkbenchMode; item?: ImprovementPoint | null }>) => {
      const mode = e.detail?.mode || null;
      if (e.detail?.item) focusItem = e.detail.item;
      workbench = mode;
      workbenchError = "";
      if (mode === "split") {
        splitDraft = [];
        void loadSplitPropose();
      } else if (mode === "conflict") {
        void loadConflictPairs();
      } else {
        paint();
      }
    }) as EventListener,
  );

  document.addEventListener(
    "studio:improvements-view-mode",
    ((e: CustomEvent<{ viewMode?: string }>) => {
      const raw = String(e.detail?.viewMode || "ips");
      const next: typeof viewMode =
        raw === "vocab"
          ? "vocab"
          : raw === "detectors"
            ? "detectors"
            : raw === "patterns"
              ? "patterns"
              : "ips";
      if (viewMode === next) {
        syncInspectorHead();
        return;
      }
      viewMode = next;
      if (viewMode === "ips") {
        focusPattern = null;
        focusSuspect = "";
        highlightWord = "";
        focusVocabWord = "";
        promoResult = null;
        promoError = "";
      }
      if (viewMode === "detectors") {
        focusPattern = null;
        focusVocabWord = "";
        promoResult = null;
        promoError = "";
        void ensureChecksOverview();
      }
      if (viewMode === "vocab") {
        focusPattern = null;
        promoResult = null;
        promoError = "";
        void ensureChecksOverview();
      }
      if (viewMode === "patterns") {
        focusSuspect = "";
        highlightWord = "";
        focusVocabWord = "";
      }
      if (copilotHost) copilotHost.hidden = viewMode !== "ips";
      syncInspectorHead();
      paint();
    }) as EventListener,
  );

  document.addEventListener(
    "studio:patterns-select",
    ((e: CustomEvent<{ pattern?: ImprovementPattern | null }>) => {
      focusPattern = e.detail?.pattern || null;
      promoResult = null;
      promoError = "";
      resetDetectorSelfTest();
      paint();
      if (focusPattern) void ensureChecksOverview();
    }) as EventListener,
  );

  document.addEventListener(
    "studio:detectors-select",
    ((e: CustomEvent<{ suspectType?: string; highlightWord?: string }>) => {
      focusSuspect = String(e.detail?.suspectType || "").trim();
      highlightWord = String(e.detail?.highlightWord || "").trim();
      resetDetectorSelfTest();
      paint();
      if (focusSuspect) void ensureChecksOverview();
    }) as EventListener,
  );

  document.addEventListener(
    "studio:vocab-select",
    ((e: CustomEvent<{ wordName?: string; suspectType?: string }>) => {
      focusVocabWord = String(e.detail?.wordName || "").trim();
      focusSuspect = String(e.detail?.suspectType || "").trim();
      highlightWord = focusVocabWord;
      resetDetectorSelfTest();
      paint();
      if (focusVocabWord) void ensureChecksOverview();
    }) as EventListener,
  );

  document.addEventListener("studio:checks-reload", (() => {
    void ensureChecksOverview(true);
  }) as EventListener);
}

function paint(): void {
  if (!bodyEl) return;
  bodyEl.innerHTML = "";
  if (viewMode === "vocab") {
    if (focusVocabWord) {
      bodyEl.appendChild(renderVocabWordBlock(focusVocabWord));
      if (focusSuspect) {
        bodyEl.appendChild(
          renderDetectorBlock({
            suspectType: focusSuspect,
            highlightWord: focusVocabWord,
          }),
        );
      }
    } else {
      bodyEl.appendChild(renderVocabEmpty());
    }
    return;
  }
  if (viewMode === "detectors") {
    if (focusSuspect) {
      bodyEl.appendChild(
        renderDetectorBlock({
          suspectType: focusSuspect,
          highlightWord,
        }),
      );
      if (highlightWord) {
        requestAnimationFrame(() => {
          bodyEl?.querySelector(".is-highlight")?.scrollIntoView({ block: "nearest" });
        });
      }
    } else {
      bodyEl.appendChild(renderDetectorEmpty());
    }
    return;
  }
  if (viewMode === "patterns") {
    bodyEl.appendChild(renderPatternBlock());
    if (focusPattern) {
      bodyEl.appendChild(
        renderDetectorBlock({
          suspectType: String(focusPattern.suspect_type || "").trim(),
          detectorRef: String(focusPattern.detector_ref || "").trim(),
          pattern: focusPattern,
        }),
      );
    }
    return;
  }
  bodyEl.appendChild(renderBookBlock());
  if (workbench === "split") {
    bodyEl.appendChild(renderSplitWorkbench());
  } else if (workbench === "conflict") {
    bodyEl.appendChild(renderConflictWorkbench());
  } else {
    bodyEl.appendChild(renderFocusBlock());
  }
  bodyEl.appendChild(renderHelpBlock());
}

/** 取一次加载器现状；已取过就不再取，除非上次失败。 */
async function ensureChecksOverview(force = false): Promise<void> {
  if (checksBusy) return;
  if (checksOverview && !force) return;
  checksBusy = true;
  checksError = "";
  paint();
  try {
    checksOverview = await corpus.checksOverview();
  } catch (e) {
    checksError = `读取检测器加载状态失败：${e}`;
  } finally {
    checksBusy = false;
    paint();
  }
}

function detectorSubhead(text: string, hint = ""): HTMLElement {
  const h = el("h5", "improvements-insp-subh", text);
  if (hint) h.appendChild(el("span", "muted improvements-detector-subhint", ` ${hint}`));
  return h;
}

/** 跑这条检测器自带的正反例。 */
async function runDetectorExamples(suspect: string): Promise<void> {
  if (!suspect || examplesBusy) return;
  examplesBusy = true;
  examplesError = "";
  paint();
  try {
    examplesResult = await corpus.runCheckExamples(suspect);
  } catch (e) {
    examplesResult = null;
    examplesError = `跑正反例失败：${e}`;
  } finally {
    examplesBusy = false;
    paint();
  }
}

/** 在这条模式的成员页上真跑一遍：证明它在真实 IR 上能跑通、且不再乱报。 */
async function runDetectorDryRun(
  suspect: string,
  jobId: string,
  pages: number[],
): Promise<void> {
  if (!suspect || !jobId || dryBusy) return;
  dryBusy = true;
  dryError = "";
  paint();
  try {
    dryResult = await corpus.dryRunCheck({
      job_id: jobId,
      suspect_type: suspect,
      pages,
      limit: 40,
    });
  } catch (e) {
    dryResult = null;
    dryError = `试跑失败：${e}`;
  } finally {
    dryBusy = false;
    paint();
  }
}

/** 「这条检测器到底准不准」：正反例回归 + 真实页试跑。 */
function renderSelfTest(
  check: SymptomCheckInfo,
  pattern?: ImprovementPattern | null,
): HTMLElement {
  const wrap = el("div", "improvements-detector-selftest");
  wrap.appendChild(
    detectorSubhead("能不能起作用", "光看判据不算数，跑一遍才知道"),
  );

  const suspect = String(check.suspect_type || pattern?.suspect_type || "").trim();
  const memberPages = (pattern?.member_pages || [])
    .map((n) => Number(n))
    .filter((n) => n > 0);
  const pages = Array.from(new Set(memberPages)).slice(0, 20);
  const jobId = String((pattern?.members || []).find((m) => m.job_id)?.job_id || "").trim();

  const actions = el("div", "improvements-insp-actions");
  const exBtn = el(
    "button",
    "btn-secondary",
    examplesBusy ? "跑正反例中…" : "跑正反例",
  ) as HTMLButtonElement;
  exBtn.type = "button";
  exBtn.disabled = examplesBusy || !check.has_examples;
  exBtn.title = check.has_examples
    ? "用声明里挂的正例/反例样例页跑一遍，算召回率与误报率"
    : "这条检测器没挂正反例，没得跑";
  exBtn.onclick = () => void runDetectorExamples(suspect);
  actions.appendChild(exBtn);

  const dryBtn = el(
    "button",
    "btn-secondary",
    dryBusy ? "试跑中…" : `在成员页上试跑（${pages.length} 页）`,
  ) as HTMLButtonElement;
  dryBtn.type = "button";
  dryBtn.disabled = dryBusy || !jobId || !pages.length;
  dryBtn.title = jobId
    ? "在这条模式当初出问题的那些页上真跑一遍：不落库、不改文件"
    : "成员里没带任务号，无法定位到具体某次导入";
  dryBtn.onclick = () => void runDetectorDryRun(suspect, jobId, pages);
  actions.appendChild(dryBtn);
  wrap.appendChild(actions);

  if (!check.has_examples) {
    wrap.appendChild(
      el(
        "p",
        "improvements-detector-warn",
        "没挂正反例 = 从没被回归验证过。判据改动后没有任何东西拦得住它变松或变紧，"
        + "晋级为 rule 的门也过不了。",
      ),
    );
  }

  if (examplesError) wrap.appendChild(el("p", "error", examplesError));
  if (examplesResult) {
    const r = examplesResult;
    const line = el(
      "p",
      r.passed
        ? "improvements-pattern-promo-ok"
        : "improvements-pattern-promo-no",
      `正反例：正例 ${r.positive_count} 个召回 ${(r.recall * 100).toFixed(0)}%，`
        + `反例 ${r.negative_count} 个误报 ${(r.false_positive_rate * 100).toFixed(0)}%`
        + ` —— ${r.verdict}`,
    );
    wrap.appendChild(line);
    const ul = el("ul", "improvements-detector-cases");
    for (const d of r.detail || []) {
      const li = el(
        "li",
        d.ok ? "gate-pass" : "gate-fail",
        `${d.polarity === "positive" ? "正例" : "反例"} ${d.ok ? "✓" : "✗"} ${d.file}`
          + `（${d.hit ? "报了" : "没报"}）`,
      );
      ul.appendChild(li);
    }
    wrap.appendChild(ul);
  }

  if (dryError) wrap.appendChild(el("p", "error", dryError));
  if (dryResult) {
    const r = dryResult;
    wrap.appendChild(
      el(
        "p",
        "improvements-detector-dry",
        `试跑：扫了 ${r.scanned_pages} 页，命中 ${r.hit_count} 处`
          + (r.hit_pages.length ? ` · 页码 ${r.hit_pages.join("、")}` : ""),
      ),
    );
    wrap.appendChild(
      el(
        "p",
        "muted",
        r.hit_count === 0
          ? "这些页当初的问题已经改好了，检测器现在不报——说明它跑得通，且不会对修好的页乱报。"
          : "这些页当初的问题已经改好，现在仍然报，说明要么没改净，要么判据偏松，逐条看证据：",
      ),
    );
    if (r.hit_count) {
      const ul = el("ul", "improvements-detector-cases");
      for (const h of (r.hits || []).slice(0, 10)) {
        const ev = h.evidence || {};
        const preview = String(ev.text_preview || "").slice(0, 40);
        const bits = Object.entries(ev)
          .filter(([k]) => k !== "text_preview")
          .map(([k, v]) => `${k}=${String(v).slice(0, 20)}`)
          .join(" · ");
        const li = el(
          "li",
          "",
          `第 ${h.page_no ?? "?"} 页 ${h.block_id || ""}`
            + (preview ? ` 「${preview}」` : "")
            + (bits ? ` — ${bits}` : ""),
        );
        ul.appendChild(li);
      }
      if (r.hit_count > 10) {
        ul.appendChild(el("li", "muted", `… 另有 ${r.hit_count - 10} 处`));
      }
      wrap.appendChild(ul);
    }
  }
  return wrap;
}

function renderDetectorEmpty(): HTMLElement {
  const box = el("section", "improvements-insp-card improvements-insp-card--detector");
  box.appendChild(el("h4", "improvements-insp-card-title", "检测器"));
  box.appendChild(
    el("p", "muted", "在中栏「检测器」里点选一条，这里显示它用到的配置词 / 词插件与判据。"),
  );
  return box;
}

function renderVocabEmpty(): HTMLElement {
  const box = el("section", "improvements-insp-card improvements-insp-card--detector");
  box.appendChild(el("h4", "improvements-insp-card-title", "词表"));
  box.appendChild(
    el("p", "muted", "在中栏「词表」里点选一个配置词 / 词插件 / 派生特征，这里显示谁在用它。"),
  );
  return box;
}

function renderVocabWordBlock(name: string): HTMLElement {
  const box = el("section", "improvements-insp-card improvements-insp-card--detector");
  box.appendChild(el("h4", "improvements-insp-card-title", "挂载层词"));
  if (checksError) {
    box.appendChild(el("p", "error", checksError));
    return box;
  }
  if (!checksOverview) {
    box.appendChild(el("p", "muted", checksBusy ? "读取中…" : "尚未读取。"));
    return box;
  }
  const word = vocabWord(name, checksOverview);
  if (!word) {
    box.appendChild(el("p", "improvements-detector-warn", `词表里没有「${name}」。`));
    return box;
  }
  box.appendChild(el("p", "improvements-insp-book-title", word.name));
  if (word.unused) {
    box.appendChild(
      el("p", "improvements-detector-warn", "孤词：没有任何检测器会因为它命中，这只眼睛等于白开。"),
    );
  }
  const kv = el("dl", "improvements-pattern-kv");
  const row = (k: string, v: string, cls = "") => {
    kv.appendChild(el("dt", "", k));
    kv.appendChild(el("dd", cls, v || "—"));
  };
  row("层级", LAYER_LABEL[word.layer] || word.layer);
  row("判定范围", word.scope || "—");
  row("来源文件", word.source || "（镜像内）");
  row("释义", word.doc || "（未写释义）");
  if (word.expr) row("表达式", word.expr);
  row("依赖", (word.depends_on || []).join("、") || "（无）");
  row("被哪些词引用", (word.used_by_words || []).join("、") || "（无）");
  const n = (word.used_by || []).length;
  row(
    "被哪些检测器使用",
    n ? `${n} 条${n > 1 ? "（共用）" : "（专用）"}` : "没有",
    word.unused ? "improvements-detector-bad" : n > 1 ? "improvements-detector-shared" : "",
  );
  box.appendChild(kv);

  const users = word.used_by || [];
  if (users.length) {
    const ul = el("ul", "improvements-detector-cases");
    for (const file of users) {
      const check = checkBySourceFile(checksOverview, file);
      const suspect = String(check?.suspect_type || "").trim();
      const label = suspect
        ? `${suspect}${check?.title ? ` · ${check.title}` : ""}`
        : file;
      const li = el("li", suspect && suspect === focusSuspect ? "is-highlight" : "", label);
      ul.appendChild(li);
    }
    box.appendChild(ul);
    box.appendChild(el("p", "muted", "在中栏点子行，可打开那条检测器的判据。"));
  }
  return box;
}

/**
 * 把加载器与检测器的实情摆出来：声明有没有在跑、判据在判什么、准不准。
 * 抽象结果页带上 pattern（才能在成员页试跑）；检测器页签只传症状名。
 */
function renderDetectorBlock(opts: {
  suspectType: string;
  detectorRef?: string;
  pattern?: ImprovementPattern | null;
  highlightWord?: string;
}): HTMLElement {
  const p = opts.pattern || null;
  const box = el("section", "improvements-insp-card improvements-insp-card--detector");
  const head = el("div", "improvements-insp-detector-head");
  head.appendChild(el("h4", "improvements-insp-card-title", "检测器 · 加载器现状"));
  const reload = el(
    "button",
    "btn-link",
    checksBusy ? "读取中…" : "刷新",
  ) as HTMLButtonElement;
  reload.type = "button";
  reload.disabled = checksBusy;
  reload.title = "重新读一次 configs/checks（改完 YAML 点这里即可，无需重建镜像）";
  reload.onclick = () => void ensureChecksOverview(true);
  head.appendChild(reload);
  box.appendChild(head);

  if (checksError) {
    box.appendChild(el("p", "error", checksError));
    return box;
  }
  if (!checksOverview) {
    box.appendChild(el("p", "muted", checksBusy ? "读取中…" : "尚未读取。"));
    return box;
  }

  const ov = checksOverview;
  const suspect = String(opts.suspectType || "").trim();
  const detectorRef = String(opts.detectorRef || p?.detector_ref || "").trim();
  const check = findCheck(ov, suspect, detectorRef);
  const file = baseName(detectorRef) || baseName(check?.source || "");
  const fileErrors = (ov.load_errors || []).filter(
    (e) => baseName(e.file) === file || (!file && false),
  );

  const status = el("p", "improvements-detector-status");
  if (check) {
    status.classList.add("is-live");
    status.textContent = check.enabled === false ? "已加载，但被停用（enabled: false）" : "已加载 · 下一本书会用它来查";
  } else if (fileErrors.length) {
    status.classList.add("is-broken");
    status.textContent = "声明写坏了，没能加载 —— 这条症状目前根本没在查";
  } else if (detectorRef) {
    status.classList.add("is-broken");
    status.textContent = `声明 ${detectorRef} 不在生效列表里（文件不存在或已改名）`;
  } else {
    status.classList.add("is-missing");
    status.textContent = "这条模式没有检测器，跨书查不出来——需要先写一份声明";
  }
  box.appendChild(status);

  if (fileErrors.length) {
    const ul = el("ul", "improvements-detector-errors");
    for (const e of fileErrors) {
      for (const msg of e.errors || []) ul.appendChild(el("li", "", msg));
    }
    box.appendChild(ul);
  }

  if (check) {
    box.appendChild(detectorSubhead("声明", "这份 YAML 里到底写了什么"));
    const kv = el("dl", "improvements-pattern-kv");
    const row = (k: string, v: string, cls = "") => {
      kv.appendChild(el("dt", "", k));
      kv.appendChild(el("dd", cls, v || "—"));
    };
    row("文件", check.source || "—");
    row("症状名", check.suspect_type || "—");
    if (check.title) row("说明", check.title);
    row(
      "严重度",
      `${SEVERITY_LABEL[check.severity || ""] || check.severity || "—"}`
        + (check.enabled === false ? " · 已停用" : ""),
      check.enabled === false ? "improvements-detector-bad" : "",
    );
    row("判定范围", SCOPE_LABEL[check.scope || ""] || check.scope || "—");
    row(
      "触发阈值",
      check.min_hits && check.min_hits > 1
        ? `整页命中 ≥ ${check.min_hits} 块才算一条`
        : "命中一块就报（min_hits: 1）",
    );
    row("证据字段", (check.evidence || []).join("、") || "（不带证据）");
    const locate = check.locate_by || [];
    row(
      "定位依据",
      locate.length
        ? `${locate.join("、")}（改进器只拿这几项）`
        : "（未声明，退回整份证据）",
    );
    const nPos = (check.examples?.positive || []).length;
    const nNeg = (check.examples?.negative || []).length;
    row(
      "正反例",
      check.has_examples
        ? `正例 ${nPos} 个 · 反例 ${nNeg} 个`
        : "没挂样例——判据准不准无从验证",
      check.has_examples ? "" : "improvements-detector-bad",
    );
    box.appendChild(kv);

    box.appendChild(detectorSubhead("判据（when）", "命中就是这个表达式为真"));
    box.appendChild(el("pre", "improvements-detector-when", check.when || "（空）"));

    const names = namesUsedIn(check.when || "");
    const rows = names
      .map((n) => ({ name: n, info: glossaryRow(n, ov) }))
      .filter((r) => r.info);
    if (rows.length) {
      box.appendChild(detectorSubhead("判据里每个词的意思"));
      const dl = el("dl", "improvements-pattern-kv improvements-detector-glossary");
      for (const r of rows) {
        const dt = el("dt", "improvements-insp-mono", r.name);
        dt.appendChild(el("span", "improvements-detector-kind", ` ${r.info!.kind}`));
        dl.appendChild(dt);
        const dd = el("dd", "", r.info!.text);
        if (opts.highlightWord && r.name === opts.highlightWord) {
          dt.classList.add("is-highlight");
          dd.classList.add("is-highlight");
        }
        dl.appendChild(dd);
      }
      box.appendChild(dl);
      const derivedUsed = derivedUsedIn(check.when || "", ov.vocabulary?.derived || []);
      const nested = derivedUsed
        .flatMap((d) => namesUsedIn(d.expr))
        .filter((n, i, arr) => arr.indexOf(n) === i)
        .filter((n) => !names.includes(n))
        .map((n) => ({ name: n, info: glossaryRow(n, ov) }))
        .filter((r) => r.info);
      if (nested.length) {
        box.appendChild(
          el("p", "muted", "派生特征展开后还用到："),
        );
        const dl2 = el("dl", "improvements-pattern-kv improvements-detector-glossary");
        for (const r of nested) {
          dl2.appendChild(el("dt", "improvements-insp-mono", r.name));
          dl2.appendChild(el("dd", "", r.info!.text));
        }
        box.appendChild(dl2);
      }
    }

    box.appendChild(detectorSubhead("命中之后做什么"));
    const act = el("dl", "improvements-pattern-kv");
    const actRow = (k: string, v: string, cls = "") => {
      act.appendChild(el("dt", "", k));
      act.appendChild(el("dd", cls, v || "—"));
    };
    const kind = String(check.remediation_kind || "").trim();
    actRow(
      "处置",
      kind === "rule"
        ? "rule：导入时按规则自动修，不需要人"
        : kind === "playbook"
          ? "playbook：只报症状 + 给手册，改动仍由 Agent/人执行"
          : "—",
    );
    actRow("生效面", check.landing_target || "（未定）");
    if (check.rule_ref) actRow("规则出处", check.rule_ref);
    const pb = String(check.playbook || "").trim();
    actRow(
      "手册",
      pb ? `${pb.split("\n")[0].slice(0, 40)}…（共 ${pb.split("\n").length} 行）` : "（无）",
      pb || kind !== "playbook" ? "" : "improvements-detector-bad",
    );
    box.appendChild(act);

    // 模式表和声明是两份数据，对不上时按声明为准——真正跑的是声明。
    if (p) {
      const mismatch: string[] = [];
      const pKind = String(p.remediation_kind || "").trim();
      if (pKind && kind && pKind !== kind) {
        mismatch.push(`处置：模式表写 ${pKind}，声明里是 ${kind}`);
      }
      const pLanding = String(p.landing_target || "").trim();
      if (pLanding && check.landing_target && pLanding !== check.landing_target) {
        mismatch.push(`生效面：模式表写 ${pLanding}，声明里是 ${check.landing_target}`);
      }
      if (mismatch.length) {
        box.appendChild(
          el(
            "p",
            "improvements-detector-warn",
            `模式表与声明对不上（真正执行的是声明）：${mismatch.join("；")}`,
          ),
        );
      }
    }

    box.appendChild(renderSelfTest(check, p));
  }

  box.appendChild(detectorSubhead("加载器", "谁在读这些声明、读进来多少"));
  const loader = el("dl", "improvements-pattern-kv");
  const lrow = (k: string, v: string, cls = "") => {
    loader.appendChild(el("dt", "", k));
    loader.appendChild(el("dd", cls, v || "—"));
  };
  const bad = (ov.load_errors || []).length;
  lrow("目录", ov.checks_dir || "configs/checks");
  lrow("生效中", `${ov.count ?? (ov.checks || []).length} 条检测器`);
  lrow(
    "加载失败",
    bad ? `${bad} 份声明（下面列出）` : "0 份",
    bad ? "improvements-detector-bad" : "",
  );
  const vocab = ov.vocabulary || { page: {}, block: {}, functions: [], derived: [], docs: {} };
  lrow(
    "可用词表",
    `块级 ${Object.keys(vocab.block || {}).length} 个 · 页级 ${
      Object.keys(vocab.page || {}).length
    } 个 · 函数 ${(vocab.functions || []).length} 个 · 派生 ${
      (vocab.derived || []).length
    } 个`,
  );
  const cov = vocab.diagnostics?.coverage;
  if (cov) {
    lrow(
      "按来源层级",
      (["L0", "L1", "L2", "L2b", "derived"] as const)
        .filter((k) => (cov[k]?.total || 0) > 0)
        .map((k) => `${LAYER_LABEL[k] || k} ${cov[k].used}/${cov[k].total} 有人用`)
        .join(" · "),
    );
  }
  const lexicons = vocab.lexicons || [];
  if (lexicons.length) {
    lrow(
      "命名词表",
      lexicons.map((x) => `${x.name}（${x.size} 词）`).join("、")
        + " — 中文例外词只能放这里，写进判据会被跨书守卫拦掉",
    );
  }
  lrow(
    "热更新",
    "检测器、配置词、命名词表、词插件都在挂载目录里，"
      + "改完按文件时间自动重读，不用重建镜像",
  );
  box.appendChild(loader);

  const orphans = vocab.diagnostics?.unused_hot || [];
  if (orphans.length) {
    box.appendChild(
      el(
        "p",
        "improvements-detector-warn",
        `孤词 ${orphans.length} 个：${orphans.join("、")}`
          + " —— 没有任何检测器会因为它们命中，这几只眼睛等于白开",
      ),
    );
  }
  const dups = vocab.diagnostics?.duplicate_expr || [];
  for (const d of dups) {
    box.appendChild(
      el(
        "p",
        "improvements-detector-warn",
        `重复定义：${d.names.join("、")} 的表达式一字不差，应合并成一个词`,
      ),
    );
  }

  const words = vocab.words || [];
  if (words.length) {
    const hot = words.filter((w) => w.layer !== "L0" && w.layer !== "L1");
    const det = el("details", "improvements-detector-derived");
    det.appendChild(
      el("summary", "", `挂载层词表（${hot.length} 条，改完保存即生效）`),
    );
    const dl = el("dl", "improvements-pattern-kv improvements-detector-glossary");
    for (const w of hot) {
      const dt = el("dt", "improvements-insp-mono", w.name);
      dt.appendChild(
        el("span", "improvements-detector-kind", ` ${LAYER_LABEL[w.layer] || w.layer}`),
      );
      if (w.unused) dt.appendChild(el("span", "improvements-detector-bad", " 孤词"));
      dl.appendChild(dt);
      const used = w.used_by.length
        ? `在用：${w.used_by.join("、")}`
        : "没有检测器在用";
      const via = w.used_by_words.length ? `（经 ${w.used_by_words.join("、")}）` : "";
      const dd = el("dd", "", [w.doc, w.expr ? `= ${w.expr}` : ""].filter(Boolean).join(" "));
      dd.appendChild(el("div", "muted", `${used}${via}`));
      dl.appendChild(dd);
    }
    det.appendChild(dl);
    box.appendChild(det);
  }

  if (bad) {
    const ul = el("ul", "improvements-detector-errors");
    for (const e of ov.load_errors || []) {
      const li = el("li");
      li.appendChild(el("code", "", e.file));
      li.appendChild(el("span", "", `：${(e.errors || []).join("；")}`));
      ul.appendChild(li);
    }
    box.appendChild(ul);
  }

  const derivedAll = vocab.derived || [];
  if (derivedAll.length) {
    const det = el("details", "improvements-detector-derived");
    det.appendChild(el("summary", "", `派生特征全表（${derivedAll.length} 条）`));
    const dl = el("dl", "improvements-pattern-kv improvements-detector-glossary");
    for (const d of derivedAll) {
      const dt = el("dt", "improvements-insp-mono", d.name);
      if (!d.page_safe) {
        dt.appendChild(el("span", "improvements-detector-kind", " 仅块级"));
      }
      dl.appendChild(dt);
      dl.appendChild(el("dd", "improvements-insp-mono", d.expr));
    }
    det.appendChild(dl);
    box.appendChild(det);
  }
  return box;
}

function renderPatternBlock(): HTMLElement {
  const box = el("section", "improvements-insp-card improvements-insp-card--focus");
  box.appendChild(el("h4", "improvements-insp-card-title", "模式详情"));
  if (!focusPattern) {
    box.appendChild(
      el("p", "muted", "在中栏「抽象结果」里点选一条模式，这里显示四元组、成员与晋级门。"),
    );
    return box;
  }
  const p = focusPattern;
  const suspect = String(p.suspect_type || "").trim() || "（未命名症状）";
  box.appendChild(el("p", "improvements-insp-book-title", suspect));
  const abs = String(p.abstract_text || "").trim();
  if (abs) box.appendChild(el("p", "improvements-insp-summary", abs));

  const metrics = el("div", "improvements-insp-metrics");
  const metric = (label: string, n: string | number) => {
    const m = el("div", "improvements-insp-metric");
    m.appendChild(el("span", "improvements-insp-metric-n", String(n)));
    m.appendChild(el("span", "muted", label));
    return m;
  };
  metrics.append(
    metric("成员", p.member_count ?? 0),
    metric("跨书", p.book_count ?? 0),
    metric("检测命中", p.hits ?? 0),
    metric("人工误报", p.false_positive ?? 0),
  );
  box.appendChild(metrics);

  box.appendChild(el("h5", "improvements-insp-subh", "四元组"));
  const landing =
    String(p.landing_target_label || "").trim() ||
    (p.landing_target
      ? LANDING_TARGETS[p.landing_target as keyof typeof LANDING_TARGETS] ||
        p.landing_target
      : "未定");
  const kv = el("dl", "improvements-pattern-kv");
  const row = (k: string, v: string) => {
    kv.appendChild(el("dt", "", k));
    kv.appendChild(el("dd", "", v || "—"));
  };
  row("症状名", suspect);
  row("检测器", String(p.detector_ref || "").trim() || "（无，跨书查不出）");
  row("处置", String(p.remediation_kind || "").trim() || "—");
  row("生效面", landing);
  {
    const raw = String(p.status || "").trim();
    const label = patternStatusLabel(raw);
    row("状态", raw && label !== raw ? `${label}（${raw}）` : label);
  }
  box.appendChild(kv);

  const playbook = String(p.playbook || "").trim();
  const playbookFrom = String(p.playbook_ref || "").trim();
  box.appendChild(el("h5", "improvements-insp-subh", "手册（playbook）"));
  if (playbook) {
    box.appendChild(el("pre", "improvements-pattern-playbook", playbook));
    if (playbookFrom) {
      box.appendChild(el("p", "muted improvements-pattern-src", `出处：${playbookFrom}`));
    }
  } else if (playbookFrom) {
    box.appendChild(
      el("p", "muted", `手册指向 ${playbookFrom}，但那份检测器里没有步骤正文。`),
    );
  } else {
    box.appendChild(el("p", "muted", "尚未写入手册步骤。"));
  }

  box.appendChild(el("h5", "improvements-insp-subh", "成员改进点"));
  const members = p.members || [];
  if (!members.length) {
    box.appendChild(el("p", "muted", "未带回成员列表（刷新模式列表即可）。"));
  } else {
    const wrap = el("div", "improvements-pattern-members");
    const table = el("table", "improvements-table");
    const thead = el("thead");
    const hr = el("tr");
    for (const lab of ["页", "摘要", "任务", "指纹"]) {
      hr.appendChild(el("th", "", lab));
    }
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const m of members) {
      const tr = el("tr");
      tr.appendChild(el("td", "", m.page_no != null ? String(m.page_no) : "—"));
      const sm = String(m.summary || "").trim();
      tr.appendChild(el("td", "", sm.length > 40 ? `${sm.slice(0, 40)}…` : sm || "—"));
      tr.appendChild(el("td", "improvements-insp-mono", String(m.job_id || "").slice(0, 8) || "—"));
      tr.appendChild(
        el("td", "improvements-insp-mono", String(m.fingerprint_hash || "").slice(0, 10) || "—"),
      );
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    box.appendChild(wrap);
  }

  box.appendChild(el("h5", "improvements-insp-subh", "晋级检查"));
  const actions = el("div", "improvements-insp-actions");
  const promoBtn = el(
    "button",
    "btn-secondary",
    promoBusy ? "检查中…" : "晋级检查",
  ) as HTMLButtonElement;
  promoBtn.type = "button";
  promoBtn.disabled = promoBusy || !p.id;
  promoBtn.title = "五道门只读检查：只出提议，不写规则文件";
  promoBtn.onclick = () => void runPromotionCheck(String(p.id));
  actions.appendChild(promoBtn);
  box.appendChild(actions);
  if (promoError) {
    box.appendChild(el("p", "error", promoError));
  }
  if (promoResult) {
    const banner = el(
      "p",
      promoResult.promotable
        ? "improvements-pattern-promo-ok"
        : "improvements-pattern-promo-no",
      promoResult.proposal || (promoResult.promotable ? "可以提议晋级" : "尚未够格晋级"),
    );
    box.appendChild(banner);
    const ul = el("ul", "improvements-pattern-gates");
    for (const g of promoResult.gates || []) {
      const li = el(
        "li",
        g.passed ? "gate-pass" : "gate-fail",
        `${g.passed ? "✓" : "✗"} ${g.gate} — ${g.detail || ""}`,
      );
      ul.appendChild(li);
    }
    box.appendChild(ul);
  } else if (!promoBusy) {
    box.appendChild(
      el("p", "muted", "点「晋级检查」看五道门（复现跨书、动作一致、可形式化、正反例、误报）。"),
    );
  }
  return box;
}

async function runPromotionCheck(patternId: string): Promise<void> {
  if (!patternId || promoBusy) return;
  promoBusy = true;
  promoError = "";
  promoResult = null;
  paint();
  try {
    promoResult = await patterns.promotionCheck(patternId);
  } catch (err) {
    promoError = `晋级检查失败：${String(err)}`;
  } finally {
    promoBusy = false;
    paint();
  }
}

function renderBookBlock(): HTMLElement {
  const box = el("section", "improvements-insp-card");
  box.appendChild(el("h4", "improvements-insp-card-title", "本书"));
  if (!ctx.jobId && !ctx.fingerprintHash) {
    box.appendChild(
      el("p", "muted", "左侧选导入任务后，这里显示该书台账摘要。"),
    );
    return box;
  }
  const title = ctx.jobLabel || (ctx.jobId ? `任务 ${ctx.jobId.slice(0, 8)}…` : "本书");
  box.appendChild(el("p", "improvements-insp-book-title", title));
  if (ctx.fingerprintHash) {
    box.appendChild(
      el("p", "muted improvements-insp-mono", `书标识 ${ctx.fingerprintHash}`),
    );
  }
  const c = ctx.counts;
  if (c) {
    const bp = c.by_prep_status || {};
    const metrics = el("div", "improvements-insp-metrics");
    const cells: Array<[string, number]> = [
      ["待整理", Number(bp.needs_triage || 0)],
      ["待拆分", Number(bp.needs_split || 0)],
      ["需聚合", Number(bp.needs_aggregate || bp.needs_conflict_resolve || 0)],
      ["完成体", Number(bp.ready || 0)],
    ];
    for (const [lab, num] of cells) {
      const cell = el("div", "improvements-insp-metric");
      cell.appendChild(el("span", "improvements-insp-metric-n", String(num)));
      cell.appendChild(el("span", "muted", lab));
      metrics.appendChild(cell);
    }
    box.appendChild(metrics);
  }
  if (ctx.fingerprintHash) {
    const actions = el("div", "improvements-insp-actions");
    const bp = ctx.counts?.by_prep_status || {};
    const nSplit = Number(bp.needs_split || 0);
    const triageBtn = el("button", "btn-primary", "整理") as HTMLButtonElement;
    triageBtn.type = "button";
    triageBtn.title = "全书：清空全部待整理 → 完成体 / 待拆分 / 需聚合";
    triageBtn.onclick = () => {
      void openImprovementsCopilot({
        intent: "triage",
        payload: {
          fingerprint_hash: ctx.fingerprintHash,
          job_id: ctx.jobId || undefined,
          job_label: ctx.jobLabel || undefined,
          process_all: true,
          limit: 2000,
          user_goal:
            "处理该书全部待整理改进点，直到 needs_triage 为 0；每批 triage_apply 后继续 list。",
        },
      }).catch((e) => window.alert(`启动整理失败：${e}`));
    };
    const splitAllBtn = el(
      "button",
      "btn-secondary",
      "全部拆分",
    ) as HTMLButtonElement;
    splitAllBtn.type = "button";
    splitAllBtn.disabled = nSplit <= 0;
    splitAllBtn.title =
      nSplit > 0
        ? `全书：对 ${nSplit} 条待拆分一次性 AI 拆分写库（与「拆分本条」不同入口）`
        : "当前没有待拆分条目";
    splitAllBtn.onclick = () => {
      if (nSplit <= 0) {
        window.alert("当前没有待拆分条目。请先「整理」标出待拆分。");
        return;
      }
      void openImprovementsCopilot({
        intent: "split_all",
        payload: {
          fingerprint_hash: ctx.fingerprintHash,
          job_id: ctx.jobId || undefined,
          job_label: ctx.jobLabel || undefined,
          process_all: true,
          limit: 2000,
          user_goal:
            "对该书全部 needs_split 逐条完备路径拆分并写库，直到 remaining_needs_split=0。",
        },
      }).catch((e) => window.alert(`启动全部拆分失败：${e}`));
    };
    const detectBtn = el(
      "button",
      "btn-secondary",
      "处理全部聚合",
    ) as HTMLButtonElement;
    detectBtn.type = "button";
    detectBtn.title =
      "全书：按簇#处理全部对立簇（中栏点簇标题则只处理那一簇）";
    detectBtn.onclick = () => {
      void openImprovementsCopilot({
        intent: "aggregate",
        payload: {
          fingerprint_hash: ctx.fingerprintHash,
          job_id: ctx.jobId || undefined,
          job_label: ctx.jobLabel || undefined,
          focus: "aggregate",
          process_all: true,
          user_goal:
            "处理该书全部需聚合簇（簇#1、簇#2…），逐簇成对说明并经确认后写库。",
        },
      }).catch((e) => window.alert(`启动全部聚合失败：${e}`));
    };
    actions.append(triageBtn, splitAllBtn, detectBtn);
    box.appendChild(actions);
  }
  return box;
}

function resolveLineageEvents(it: ImprovementPoint): IpLineageEvent[] {
  if (Array.isArray(it.lineage) && it.lineage.length) return it.lineage;
  const fromLife = (it.lifecycle?.stages || []).find((s) => s.key === "lineage");
  const events = (fromLife as { events?: unknown } | undefined)?.events;
  if (Array.isArray(events)) {
    return events.filter((e) => e && typeof e === "object") as IpLineageEvent[];
  }
  // 弱回填：concrete/claim 仅有 split_from
  const concrete = (it.concrete || {}) as Record<string, unknown>;
  const claim = (it.claim || {}) as Record<string, unknown>;
  const sf = String(concrete.split_from || claim.split_from || "").trim();
  if (!sf) return [];
  const idx = concrete.split_index ?? claim.split_index;
  return [
    {
      op: "split",
      summary: `拆自父条 ${sf.slice(0, 16)}…（父正文已不可用）`,
      parent: { id: sf, summary: "" },
      meta: { split_from: sf, split_index: idx, weak: true },
      weak: true,
    },
  ];
}

function renderLineageBlock(box: HTMLElement, it: ImprovementPoint): void {
  const events = resolveLineageEvents(it);
  const wrap = el("div", "improvements-lineage");
  wrap.appendChild(el("div", "improvements-life-step-title", "改进历程（拆分 / 聚合）"));
  if (!events.length) {
    para(
      wrap,
      "尚无拆分/聚合记录（本条未经拆分或聚合，或为改版前历史数据）。",
      "muted",
    );
    box.appendChild(wrap);
    return;
  }
  para(
    wrap,
    "后来怎么整理出来的：拆自谁、聚合时删了谁（删旧条前留下的摘要）。",
    "muted",
  );
  const track = el("ol", "improvements-lineage-track");
  for (const ev of events) {
    const li = el("li", "improvements-lineage-event");
    const op = String(ev.op || "").trim();
    const opLab =
      op === "split" ? "拆分" : op === "aggregate_keep" ? "聚合保留" : op || "历程";
    const at = String(ev.at || "").trim().slice(0, 19);
    const head = el("div", "improvements-lineage-event-head");
    head.appendChild(el("span", "improvements-lineage-op", opLab));
    if (at) head.appendChild(el("span", "muted", at));
    if (ev.weak) head.appendChild(el("span", "muted", "残片"));
    li.appendChild(head);
    const sum = String(ev.summary || "").trim();
    if (sum) para(li, sum, "improvements-lineage-summary");
    const parent = ev.parent;
    if (parent && (parent.summary || parent.id)) {
      const pSum = String(parent.summary || "").trim();
      para(
        li,
        pSum
          ? `父条：${pSum}`
          : `父条 id：${String(parent.id || "").slice(0, 16)}…`,
        "muted improvements-lineage-detail",
      );
    }
    const peers = Array.isArray(ev.peers) ? ev.peers : [];
    if (peers.length) {
      const ul = el("ul", "improvements-insp-list improvements-lineage-peers");
      for (const peer of peers.slice(0, 12)) {
        const ps = String(peer.summary || "").trim();
        const pid = String(peer.id || "").slice(0, 8);
        ul.appendChild(
          el(
            "li",
            "",
            ps ? `对立方：${ps}` : `对立方 id：${pid}…`,
          ),
        );
      }
      li.appendChild(ul);
    }
    const meta = ev.meta || {};
    const bits: string[] = [];
    if (meta.split_count != null && meta.split_index != null) {
      bits.push(`子条 ${Number(meta.split_index) + 1}/${meta.split_count}`);
    }
    if (meta.aggregate_reason) bits.push(String(meta.aggregate_reason).slice(0, 80));
    if (bits.length) para(li, bits.join(" · "), "muted");
    track.appendChild(li);
  }
  wrap.appendChild(track);
  box.appendChild(wrap);
}

function renderLifecycleStages(box: HTMLElement, it: ImprovementPoint): void {
  const life = it.lifecycle;
  const stages = (life?.stages || []).filter((s) => s.key !== "lineage");
  if (!stages.length) {
    para(box, "生命周期故事尚未生成（请刷新或重建 buddy-api）。", "muted");
    return;
  }

  const track = el("div", "improvements-life-track");
  track.appendChild(
    el("div", "improvements-life-step-title", "完整生命周期（出生故事）"),
  );
  for (const st of stages) {
    const step = el("div", `improvements-life-step improvements-life-step--${st.key || "x"}`);
    step.appendChild(el("div", "improvements-life-step-title", st.title || ""));
    if (st.body) preBlock(step, st.body);

    const texts = (st.items || []).filter((x) => x.text && !x.tool);
    if (texts.length && st.key === "user_input") {
      para(step, "同轮其他用户消息（聚焦前 / 后续确认，均为原文）：", "muted");
      for (const t of texts) preBlock(step, String(t.text));
    }

    const tools = (st.items || []).filter((x) => x.tool);
    if (tools.length) {
      const ol = el("ol", "improvements-procedure");
      for (const t of tools) {
        ol.appendChild(
          el(
            "li",
            "",
            `${t.tool || "?"} — ${String(t.args_summary || "").slice(0, 240)}`,
          ),
        );
      }
      step.appendChild(ol);
    } else if (st.key === "tools") {
      para(step, "未记录具体工具步骤（历史补录可能过简）。", "muted");
    }

    const avoid = st.avoid || [];
    if (avoid.length) {
      para(step, "曾尝试但未保留：", "muted");
      const ul = el("ul", "improvements-insp-list");
      for (const a of avoid) {
        ul.appendChild(
          el(
            "li",
            "",
            `${a.tool || "?"} — ${String(a.args_summary || "").slice(0, 160)}`,
          ),
        );
      }
      step.appendChild(ul);
    }

    if (st.key === "outcome" && texts.length) {
      para(step, st.paths_label || "涉及文件", "muted");
      const ul = el("ul", "improvements-insp-list improvements-insp-paths");
      for (const t of texts.slice(0, 24)) {
        ul.appendChild(el("li", "improvements-insp-mono", String(t.text)));
      }
      step.appendChild(ul);
    }

    if (st.key === "lifecycle") {
      const meta = el("ul", "improvements-insp-list");
      for (const line of st.timeline || []) {
        meta.appendChild(el("li", "", line));
      }
      step.appendChild(meta);
      if ((st.next_actions || []).length) {
        para(step, "接下来要做什么：", "muted");
        const ol = el("ol", "improvements-procedure improvements-life-next");
        for (const a of st.next_actions || []) ol.appendChild(el("li", "", a));
        step.appendChild(ol);
      }
    }

    track.appendChild(step);
  }
  box.appendChild(track);
}

function renderFocusBlock(): HTMLElement {
  const box = el("section", "improvements-insp-card improvements-insp-card--focus");
  box.appendChild(el("h4", "improvements-insp-card-title", "当前选中 · 详情"));

  if (!focusItem) {
    box.appendChild(
      el(
        "p",
        "muted",
        "在中栏点击一行，这里展示改进历程与出生故事（用户→AI→工具→结果）。",
      ),
    );
    return box;
  }

  const it = focusItem;
  const id = String(it.id || "");
  const claim = (it.claim || {}) as Record<string, unknown>;
  const rawSummary = String(claim.summary || it.summary || "").trim();
  const headline = claimSummary(it) || "(无摘要)";
  const prep = prepOf(it);

  box.appendChild(el("p", "improvements-insp-summary", headline));
  if (rawSummary && rawSummary !== headline) {
    para(box, `原始摘要：${rawSummary}`, "muted improvements-insp-raw-sum");
  }
  const originShow =
    String(it.origin || "") === "ai_auto"
      ? "AI 自主改"
      : String(it.origin || "") === "user_ask"
        ? "用户让 AI 改"
        : it.origin_label || "用户让 AI 改";
  box.appendChild(
    el(
      "p",
      "muted",
      `p${it.page_no ?? "?"} · ${originShow} · ${it.category || "other"} · 专书页补丁`,
    ),
  );
  const prepBadge = el(
    "span",
    `improvements-prep-badge improvements-prep-badge--${prep}`,
    prepLabel(it),
  );
  const prepLine = el("p", "improvements-insp-prep-line");
  prepLine.appendChild(el("span", "muted", "整理状态："));
  prepLine.appendChild(prepBadge);
  box.appendChild(prepLine);

  const tr = it.trigger || {};
  const ctxBits = [
    tr.authority_label || (it.authority ? `权限 ${it.authority}` : ""),
    tr.mode_label || it.mode_label || "",
    tr.job_id || it.source_job_id ? `任务 ${(tr.job_id || it.source_job_id || "").slice(0, 8)}…` : "",
    tr.session_id || it.source_session_id
      ? `会话 ${(tr.session_id || it.source_session_id || "").slice(0, 8)}…`
      : "",
  ].filter(Boolean);
  if (ctxBits.length) {
    para(box, ctxBits.join(" · "), "muted improvements-insp-ctx");
  }

  renderLineageBlock(box, it);
  renderLifecycleStages(box, it);
  para(box, `记录 id：${id}`, "muted improvements-insp-mono");

  const actions = el("div", "improvements-insp-actions");
  const openBtn = el("button", "btn-primary", "打开页") as HTMLButtonElement;
  openBtn.type = "button";
  const canOpen = !!ctx.jobId && Number(it.page_no) > 0;
  openBtn.disabled = !canOpen;
  openBtn.title = canOpen ? "打开对应页预览" : "全书/会话级事件无单页可开";
  openBtn.onclick = () => openPage(Number(it.page_no) || 0);
  actions.appendChild(openBtn);

  if (prep === "needs_split") {
    const splitBtn = el("button", "btn-primary", "拆分本条") as HTMLButtonElement;
    splitBtn.type = "button";
    splitBtn.title = "单条入口：拆分工作台（提案 → 确认）；不是「全部拆分」";
    splitBtn.onclick = () => {
      workbench = "split";
      void loadSplitPropose();
    };
    actions.appendChild(splitBtn);
  } else if (prep === "needs_aggregate") {
    const conflictBtn = el("button", "btn-primary", "处理聚合") as HTMLButtonElement;
    conflictBtn.type = "button";
    conflictBtn.title = "进入聚合工作台：成对裁决";
    conflictBtn.onclick = () => {
      workbench = "conflict";
      void loadConflictPairs();
    };
    actions.appendChild(conflictBtn);
  } else if (prep === "needs_triage") {
    para(box, "待整理：请点「整理」让 Cursor Agent 分类。", "muted improvements-insp-ready-hint");
  } else {
    para(box, "已是完成体（可进入抽象）。", "muted improvements-insp-ready-hint");
  }

  if (it.status === "accepted" && prep === "ready") {
    const sum = claimSummary(it);
    const noneBtn = el("button", "btn-secondary", "保持仅本书") as HTMLButtonElement;
    noneBtn.type = "button";
    noneBtn.onclick = async () => {
      await setPattern(id, "none", sum);
      refreshEvent();
    };
    const draftBtn = el("button", "btn-secondary", "标成可复用草案") as HTMLButtonElement;
    draftBtn.type = "button";
    draftBtn.onclick = async () => {
      await setPattern(id, "draft", sum);
      refreshEvent();
    };
    actions.append(noneBtn, draftBtn);
  }

  if (it.status === "conflict") {
    const win = el("button", "btn-primary", "留下这条") as HTMLButtonElement;
    win.type = "button";
    win.onclick = async () => {
      await improvementPoints.resolveConflict(id, true);
      focusItem = null;
      refreshEvent();
    };
    const lose = el("button", "btn-secondary", "不要这条") as HTMLButtonElement;
    lose.type = "button";
    lose.onclick = async () => {
      await improvementPoints.resolveConflict(id, false);
      focusItem = null;
      refreshEvent();
    };
    actions.append(win, lose);
  }

  const clearBtn = el("button", "btn-link", "取消选中") as HTMLButtonElement;
  clearBtn.type = "button";
  clearBtn.onclick = () => {
    focusItem = null;
    document.dispatchEvent(
      new CustomEvent("studio:improvements-select", { detail: { item: null } }),
    );
    paint();
  };
  actions.appendChild(clearBtn);
  box.appendChild(actions);
  return box;
}

async function loadSplitPropose(): Promise<void> {
  if (!focusItem?.id) {
    workbenchError = "请先选中一条待拆分记录。";
    paint();
    return;
  }
  workbenchBusy = true;
  workbenchError = "";
  paint();
  try {
    const out = await improvementPoints.splitPropose(String(focusItem.id));
    if (out.needs_agent || !out.ok) {
      splitNeedsAgent = true;
      splitUnassignedAfter = out.unassigned_after_indices || [];
      splitDraft = [];
      workbenchError =
        (out.errors || []).join("; ") ||
        "规则快路径不适用，将启动 Cursor Agent 完备路径拆分…";
      paint();
      try {
        await openImprovementsCopilot({
          intent: "split_one",
          payload: {
            ip_id: String(focusItem.id),
            fingerprint_hash:
              ctx.fingerprintHash || focusItem.fingerprint_hash || "",
            process_all: false,
          },
        });
        workbenchError += "\n已打开上方 AI 对话；完成后回到此台确认或刷新。";
      } catch (ce) {
        workbenchError += `\n启动 Agent 失败：${ce}`;
      }
    } else {
      splitNeedsAgent = false;
      splitUnassignedAfter = out.unassigned_after_indices || [];
      splitDraft = (out.proposals || []).map((p) => ({
        summary: String(p.summary || ""),
        category: p.category,
        procedure_slice: p.procedure_slice,
        step_indices: p.step_indices,
        after_indices: p.after_indices,
        after: p.after,
      }));
      if (splitDraft.length < 2) {
        workbenchError = "提案不足 2 条。";
      }
      if (splitUnassignedAfter.length) {
        workbenchError =
          (workbenchError ? workbenchError + "；" : "") +
          `未分配 after: ${splitUnassignedAfter.join(",")}（确认已禁用）`;
      }
    }
  } catch (e) {
    workbenchError = String(e);
    splitDraft = [];
  } finally {
    workbenchBusy = false;
    paint();
  }
}

function renderSplitWorkbench(): HTMLElement {
  const box = el("section", "improvements-insp-card improvements-workbench");
  box.appendChild(el("h4", "improvements-insp-card-title", "拆分工作台"));
  if (!focusItem) {
    para(box, "未选中待拆分行。", "muted");
    const back = el("button", "btn-link", "返回") as HTMLButtonElement;
    back.type = "button";
    back.onclick = () => closeWorkbench();
    box.appendChild(back);
    return box;
  }
  para(box, "原摘要", "muted");
  preBlock(box, String(focusItem.summary || claimSummary(focusItem) || ""));
  if (workbenchBusy) {
    para(box, "正在拉取拆分提案…", "muted");
    return box;
  }
  if (workbenchError) {
    para(box, workbenchError, "error");
  }
  para(box, "子条（可编辑；确认后旧条删除、新条入库）", "muted");
  const list = el("div", "improvements-split-list");
  splitDraft.forEach((ch, idx) => {
    const row = el("div", "improvements-split-row");
    row.appendChild(el("span", "muted", `${idx + 1}.`));
    const ta = el("textarea", "edition-ops-input improvements-split-ta") as HTMLTextAreaElement;
    ta.rows = 2;
    ta.value = ch.summary;
    ta.oninput = () => {
      splitDraft[idx] = { ...ch, summary: ta.value };
    };
    row.appendChild(ta);
    const rm = el("button", "btn-link", "删") as HTMLButtonElement;
    rm.type = "button";
    rm.onclick = () => {
      splitDraft = splitDraft.filter((_, i) => i !== idx);
      paint();
    };
    row.appendChild(rm);
    list.appendChild(row);
  });
  box.appendChild(list);
  const addBtn = el("button", "btn-link", "+ 加一条") as HTMLButtonElement;
  addBtn.type = "button";
  addBtn.onclick = () => {
    splitDraft.push({ summary: "", category: focusItem?.category || "other" });
    paint();
  };
  box.appendChild(addBtn);

  const actions = el("div", "improvements-insp-actions");
  const confirmBtn = el("button", "btn-primary", "确认拆分") as HTMLButtonElement;
  confirmBtn.type = "button";
  const completeEnough =
    !splitNeedsAgent &&
    !splitUnassignedAfter.length &&
    splitDraft.filter((c) => c.summary.trim()).length >= 2 &&
    splitDraft.every(
      (c) =>
        (Array.isArray(c.step_indices) && c.step_indices.length > 0) ||
        (c.procedure_slice && typeof c.procedure_slice === "object"),
    );
  confirmBtn.disabled = workbenchBusy || !completeEnough;
  confirmBtn.title = completeEnough
    ? "确认后写库"
    : "提案不完备或 after 未分完，确认已禁用";
  confirmBtn.onclick = () => void applySplit();
  const repropose = el("button", "btn-secondary", "重新提案") as HTMLButtonElement;
  repropose.type = "button";
  repropose.onclick = () => void loadSplitPropose();
  const cancel = el("button", "btn-link", "取消") as HTMLButtonElement;
  cancel.type = "button";
  cancel.onclick = () => closeWorkbench();
  actions.append(confirmBtn, repropose, cancel);
  box.appendChild(actions);
  return box;
}

async function applySplit(): Promise<void> {
  if (!focusItem?.id) return;
  if (splitNeedsAgent || splitUnassignedAfter.length) {
    window.alert("提案不完备或 after 未分完，无法确认。");
    return;
  }
  const children = splitDraft
    .map((c) => ({ ...c, summary: c.summary.trim() }))
    .filter((c) => c.summary);
  if (children.length < 2) {
    window.alert("至少需要 2 条子条。");
    return;
  }
  workbenchBusy = true;
  paint();
  try {
    await improvementPoints.splitApply(String(focusItem.id), children);
    focusItem = null;
    closeWorkbench();
    refreshEvent();
  } catch (e) {
    workbenchError = String(e);
    workbenchBusy = false;
    paint();
  }
}

async function loadConflictPairs(): Promise<void> {
  const fp = ctx.fingerprintHash || String(focusItem?.fingerprint_hash || "");
  if (!fp) {
    workbenchError = "缺少书标识，无法加载聚合簇。";
    paint();
    return;
  }
  workbenchBusy = true;
  workbenchError = "";
  paint();
  try {
    const out = await improvementPoints.conflictPairs(fp, true);
    conflictPairs = out.pairs || [];
    conflictChoices.clear();
    for (const p of conflictPairs) {
      conflictChoices.set(pairKey(p), "keep_a");
    }
    if (!conflictPairs.length) {
      workbenchError = "未检出需聚合对（或已全部处理）。";
    }
  } catch (e) {
    workbenchError = String(e);
    conflictPairs = [];
  } finally {
    workbenchBusy = false;
    paint();
  }
}

function renderConflictWorkbench(): HTMLElement {
  const box = el("section", "improvements-insp-card improvements-workbench");
  box.appendChild(el("h4", "improvements-insp-card-title", "聚合工作台"));
  para(
    box,
    "成对对照后选择保留哪一方（或都删）。确认后才写库；取消不落库。",
    "muted",
  );
  const detectBtn = el("button", "btn-secondary", "刷新聚合对") as HTMLButtonElement;
  detectBtn.type = "button";
  detectBtn.disabled = workbenchBusy || !ctx.fingerprintHash;
  detectBtn.onclick = () => void loadConflictPairs();
  box.appendChild(detectBtn);

  if (workbenchBusy) {
    para(box, "加载中…", "muted");
    return box;
  }
  if (workbenchError) {
    para(box, workbenchError, conflictPairs.length ? "muted" : "error");
  }

  const list = el("div", "improvements-conflict-list");
  for (const p of conflictPairs) {
    const key = pairKey(p);
    const card = el("div", "improvements-conflict-pair");
    card.appendChild(
      el(
        "p",
        "muted",
        `p${p.page_no ?? "?"} · ${p.reason || "对立"}`,
      ),
    );
    const cols = el("div", "improvements-conflict-cols");
    const colA = el("div", "improvements-conflict-col");
    colA.appendChild(el("div", "improvements-conflict-col-h", "摘要 A"));
    preBlock(colA, String(p.summary_a || ""));
    const colB = el("div", "improvements-conflict-col");
    colB.appendChild(el("div", "improvements-conflict-col-h", "摘要 B"));
    preBlock(colB, String(p.summary_b || ""));
    cols.append(colA, colB);
    card.appendChild(cols);

    const choiceRow = el("div", "improvements-conflict-choices");
    const cur = conflictChoices.get(key) || "keep_a";
    const opts: Array<{ id: "keep_a" | "keep_b" | "delete_both"; lab: string }> = [
      { id: "keep_a", lab: "保留 A" },
      { id: "keep_b", lab: "保留 B" },
      { id: "delete_both", lab: "都删" },
    ];
    for (const o of opts) {
      const b = el(
        "button",
        `improvements-chip${cur === o.id ? " active" : ""}`,
        o.lab,
      ) as HTMLButtonElement;
      b.type = "button";
      b.onclick = () => {
        conflictChoices.set(key, o.id);
        paint();
      };
      choiceRow.appendChild(b);
    }
    card.appendChild(choiceRow);
    list.appendChild(card);
  }
  box.appendChild(list);

  const actions = el("div", "improvements-insp-actions");
  const confirmBtn = el("button", "btn-primary", "确认聚合裁决") as HTMLButtonElement;
  confirmBtn.type = "button";
  confirmBtn.disabled = workbenchBusy || !conflictPairs.length;
  confirmBtn.onclick = () => void applyConflictResolve();
  const cancel = el("button", "btn-link", "取消") as HTMLButtonElement;
  cancel.type = "button";
  cancel.onclick = () => closeWorkbench();
  actions.append(confirmBtn, cancel);
  box.appendChild(actions);
  return box;
}

async function applyConflictResolve(): Promise<void> {
  const keep = new Set<string>();
  const del = new Set<string>();
  for (const p of conflictPairs) {
    const ch = conflictChoices.get(pairKey(p)) || "keep_a";
    if (ch === "keep_a") {
      keep.add(p.a_id);
      del.add(p.b_id);
    } else if (ch === "keep_b") {
      keep.add(p.b_id);
      del.add(p.a_id);
    } else {
      del.add(p.a_id);
      del.add(p.b_id);
    }
  }
  for (const id of del) keep.delete(id);
  workbenchBusy = true;
  paint();
  try {
    await improvementPoints.conflictResolve({
      keep_ids: [...keep],
      delete_ids: [...del],
    });
    focusItem = null;
    closeWorkbench();
    refreshEvent();
  } catch (e) {
    workbenchError = String(e);
    workbenchBusy = false;
    paint();
  }
}

function glossSection(
  title: string,
  items: Array<[string, string]>,
  open = true,
): HTMLElement {
  const details = el("details", "improvements-gloss") as HTMLDetailsElement;
  details.open = open;
  details.appendChild(el("summary", "improvements-gloss-sum", title));
  const dl = el("dl", "improvements-gloss-list");
  for (const [term, def] of items) {
    dl.appendChild(el("dt", "", term));
    dl.appendChild(el("dd", "", def));
  }
  details.appendChild(dl);
  return details;
}

function renderHelpBlock(): HTMLElement {
  const box = el("section", "improvements-insp-card improvements-insp-help");
  box.appendChild(el("h4", "improvements-insp-card-title", "速查"));
  box.appendChild(
    el(
      "p",
      "muted improvements-gloss-lead",
      "进表即有效（专书页补丁）。整理状态 ≠ 有效/旧版：整理是预抽象卫生；status=accepted 仍表示进表有效。",
    ),
  );

  box.appendChild(
    glossSection("整理流程", [
      ["① 待整理", "进表默认；点「整理」由 Cursor Agent 分类。"],
      ["② 待拆分", "完备路径拆分（Agent/规则）→「确认拆分」。"],
      ["③ 需聚合", "对立簇 → 点「簇#n」或「处理全部聚合」→ 保留/删除。"],
      ["④ 完成体", "可挂可复用草案。"],
    ]),
  );

  box.appendChild(
    glossSection("来源（只有两类）", [
      [
        "AI 自主改",
        "导入/质检过程中 AI 自行改对并记入（origin=ai_auto）。",
      ],
      [
        "用户让 AI 改",
        "人在 Studio 聚焦改页、确认卡或 Agent 会话里改对后记入（origin=user_ask）。",
      ],
    ]),
  );

  box.appendChild(
    glossSection("和「下次导入」的关系", [
      [
        "专书页补丁（默认）",
        "同指纹重导时回放非空 after。改进点阶段不预分 L0/L1。",
      ],
      [
        "可复用草案（可选）",
        "仅完成体可标 pattern_status=draft；非 ready 服务端 403。",
      ],
    ]),
  );

  box.appendChild(
    glossSection(
      "列表操作",
      [
        ["整理", "一书一对话 · 意图 triage；写库走 MCP→api。"],
        ["全部拆分", "同一对话 · 意图 split_all；全书待拆分一次性写库。"],
        ["拆分本条", "同一对话 · 意图 split_one；行内/详情工作台。"],
        ["处理全部聚合", "同一对话 · aggregate · 全书；中栏簇#n 仅单簇。"],
        ["删除", "硬删改进点及专书页补丁等派生记录。"],
      ],
      false,
    ),
  );

  return box;
}

function openPage(pageNo: number): void {
  if (!ctx.jobId) {
    window.alert("请先在左侧选择导入任务，才能打开对应页预览。");
    return;
  }
  store.setActiveRibbon("import");
  focusMinerUJobTriPane(ctx.jobId, pageNo > 0 ? pageNo : undefined);
}

async function setPattern(
  id: string,
  status: "none" | "draft",
  summary: string,
): Promise<void> {
  await improvementPoints.setPattern(id, {
    pattern_status: status,
    abstract_text: status === "draft" ? summary : "",
    target_layer: "L1",
  });
}

