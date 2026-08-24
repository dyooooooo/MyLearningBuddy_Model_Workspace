/**
 * 改进点管理台（Ribbon「改进点」中栏）。
 *
 * 中栏：筛选 + 列表（点行 → 右栏详情）。
 * 右栏：批量操作 / 本行详情与裁决（见 ImprovementsInspector）。
 */
import {
  improvementPoints,
  LANDING_TARGETS,
  normalizePrepStatus,
  patterns,
  patternStatusLabel,
  PREP_STATUS_LABELS,
  type ImprovementPattern,
  type ImprovementPoint,
  type IpListOut,
  type PrepStatus,
} from "../api/improvementPoints";
import {
  corpus,
  type ChecksOverview,
  type SymptomCheckInfo,
} from "../api/corpus";
import { store } from "../state/store";
import {
  checkBySourceFile,
  hotWords,
  LAYER_LABEL,
  mountedWordsUsedBy,
  SCOPE_LABEL_SHORT,
  shareBadge,
} from "../util/detectorVocab";
import {
  loadPersistedIpFocus,
  openImprovementsCopilot,
  openPatternAbstractCopilot,
  persistIpFocus,
} from "./ImprovementsInspector";
import { el } from "../util/dom";

type PrepFilter = "" | PrepStatus;

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

const CAT_LABEL: Record<string, string> = {
  math: "公式",
  figure: "插图",
  layout: "版式",
  header: "页眉",
  title: "标题",
  exercise: "习题",
  table: "表格",
  other: "其它",
};

const PATTERN_LABEL: Record<string, string> = {
  none: "仅本书",
  draft: "可复用草案",
  approved: "已批准",
  landed: "已落地",
  verified: "已验证",
};

/** 状态：唯一主筛选 */
type StatusFilter =
  | "all"
  | "accepted"
  | "pending"
  | "conflict"
  | "superseded"
  | "rejected";

/** 排列：怎么摆，不与状态抢权 */
type ArrangeMode =
  | "list"
  | "by_dialogue"
  | "by_problem"
  | "by_pattern"
  | "by_aggregate";

/** 中栏一级切换：改进点台账 / 抽象结果 / 检测器目录 */
type ViewMode = "ips" | "patterns" | "detectors" | "vocab";

type VocabChipFilter = "all" | "L2" | "L2b" | "derived" | "unused";

/** 抽象结果芯片筛 */
type PatternChipFilter =
  | "all"
  | "has_detector"
  | "no_detector"
  | "playbook"
  | "rule";

function aggregateGroupIdOf(it: ImprovementPoint): string {
  const top = String(it.aggregate_group_id || "").trim();
  if (top) return top;
  const c = it.concrete;
  if (c && typeof c === "object") {
    return String((c as Record<string, unknown>).aggregate_group_id || "").trim();
  }
  return "";
}

function aggregateReasonOf(it: ImprovementPoint): string {
  const top = String(it.aggregate_reason || "").trim();
  if (top) return top;
  const c = it.concrete;
  if (c && typeof c === "object") {
    return String((c as Record<string, unknown>).aggregate_reason || "").trim();
  }
  return "";
}

/** 无 aggregate_group_id 时，用对立对做并查集合成簇。 */
function unionFindGroups(
  ids: string[],
  pairs: Array<{ a_id?: string; b_id?: string }>,
): Map<string, string[]> {
  const parent = new Map<string, string>();
  const find = (x: string): string => {
    let p = parent.get(x) || x;
    while ((parent.get(p) || p) !== p) p = parent.get(p) || p;
    let cur = x;
    while (cur !== p) {
      const n = parent.get(cur) || cur;
      parent.set(cur, p);
      cur = n;
    }
    return p;
  };
  const unite = (a: string, b: string) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };
  for (const id of ids) parent.set(id, id);
  for (const p of pairs) {
    const a = String(p.a_id || "").trim();
    const b = String(p.b_id || "").trim();
    if (a && b && parent.has(a) && parent.has(b)) unite(a, b);
  }
  const groups = new Map<string, string[]>();
  for (const id of ids) {
    const r = find(id);
    const arr = groups.get(r) || [];
    arr.push(id);
    groups.set(r, arr);
  }
  return groups;
}

/** 表头双击排序字段 */
type SortKey = "page" | "origin" | "category" | "summary";

type SortDir = "asc" | "desc";

const ORIGIN_LABEL: Record<string, string> = {
  ai_auto: "AI 自主改",
  user_ask: "用户让 AI 改",
  // 兼容清理前旧码
  fullbook_ai: "AI 自主改",
  agent_page: "用户让 AI 改",
  human_confirm: "用户让 AI 改",
  migrated_l3: "用户让 AI 改",
};

const PENDING_STATUSES = new Set(["observed", "clustered", "candidate"]);

const SORT_HEADERS: Array<{
  key: SortKey | "";
  label: string;
  kind: "check" | "sort" | "action" | "prep";
}> = [
  { key: "", label: "", kind: "check" },
  { key: "page", label: "页", kind: "sort" },
  { key: "origin", label: "来源", kind: "sort" },
  { key: "category", label: "类别", kind: "sort" },
  { key: "", label: "整理", kind: "prep" },
  { key: "summary", label: "摘要", kind: "sort" },
  { key: "", label: "", kind: "action" },
];

function catLabel(c?: string): string {
  const k = (c || "other").toLowerCase();
  return CAT_LABEL[k] || c || "其它";
}

function patternLabel(p?: string): string {
  const k = (p || "none").toLowerCase();
  return PATTERN_LABEL[k] || p || "仅本书";
}

function originLabel(it: ImprovementPoint): string {
  const code = String(it.origin || "").toLowerCase();
  if (ORIGIN_LABEL[code]) return ORIGIN_LABEL[code];
  const lab = String(it.origin_label || "").trim();
  if (lab === "AI 自主改" || lab === "用户让 AI 改") return lab;
  if (lab.includes("全文") || lab.includes("自主")) return "AI 自主改";
  return lab || "用户让 AI 改";
}

function claimSummary(it: ImprovementPoint): string {
  // 列表摘要 = 直奔主题的 summary 列（删/留/勿/改为），不用操控口令拼出来的 headline
  const col = String(it.summary || "").trim();
  if (col) return col;
  const claim = (it.claim || {}) as Record<string, unknown>;
  const claimSum = String(claim.summary || "").trim();
  if (claimSum) return claimSum;
  const headline = String(it.headline || "").trim();
  if (headline && !headline.includes("记入当前") && !headline.includes("待审队列")) {
    return headline;
  }
  return String(it.locus_key || it.id || "").trim();
}

function sortValue(it: ImprovementPoint, key: SortKey): string | number {
  switch (key) {
    case "page":
      return Number(it.page_no) || 0;
    case "origin":
      return originLabel(it).toLowerCase();
    case "category":
      return String(it.category || "").toLowerCase();
    case "summary":
      return claimSummary(it).toLowerCase();
  }
}

function sortItems(
  items: ImprovementPoint[],
  key: SortKey | "",
  dir: SortDir,
): ImprovementPoint[] {
  if (!key) return items;
  const mul = dir === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    const va = sortValue(a, key);
    const vb = sortValue(b, key);
    if (typeof va === "number" && typeof vb === "number") {
      if (va === vb) return 0;
      return va < vb ? -mul : mul;
    }
    return String(va).localeCompare(String(vb), "zh") * mul;
  });
}

function hintForList(counts: IpListOut["counts"]): string {
  const n = (k: keyof NonNullable<IpListOut["counts"]>) => Number(counts?.[k] ?? 0);
  return `这本书共 ${n("accepted") || n("total")} 条成功改进点（专书页补丁）。来源分「AI 自主改」与「用户让 AI 改」。`;
}

export function mountImprovementsPanel(root: HTMLElement): { refresh: () => void } {
  root.innerHTML = "";
  root.classList.add("improvements-panel", "edition-ops-panel");

  let jobId = "";
  let fingerprintHash = "";
  let jobLabel = "";
  /** 列表只展示成功改进点（accepted/effective）；清理后不应再有其它状态 */
  const statusSelected = new Set<Exclude<StatusFilter, "all">>(["accepted"]);
  let arrange: ArrangeMode = "list";
  /** 来源 / 类别复选；空 = 全部 */
  const originSelected = new Set<string>();
  const catSelected = new Set<string>();
  let pageFilter = "";
  let keyword = "";
  let prepFilter: PrepFilter = "";
  let lastData: IpListOut | null = null;
  /** 对立对缓存：无 aggregate_group_id 时并查集成簇 */
  let conflictPairCache: Array<{ a_id?: string; b_id?: string; reason?: string }> =
    [];
  let conflictPairFp = "";
  const selected = new Set<string>();
  let focusId = "";
  /** 「按需聚合」下当前聚焦的簇 key（aggregate_group_id 或合成 pair-cluster-n） */
  let focusClusterKey = "";
  let sortKey: SortKey | "" = "page";
  let sortDir: SortDir = "asc";
  let viewMode: ViewMode = "ips";
  let patternChip: PatternChipFilter = "all";
  let patternKeyword = "";
  let lastPatterns: ImprovementPattern[] = [];
  let patternsLoaded = false;
  let patternsBusy = false;
  let focusPatternId = "";
  let lastChecks: ChecksOverview | null = null;
  let checksLoaded = false;
  let checksBusy = false;
  let checksError = "";
  let detectorKeyword = "";
  let focusSuspect = "";
  let focusWord = "";
  let vocabKeyword = "";
  let vocabChip: VocabChipFilter = "all";
  let focusVocabWord = "";

  const ensureAggregatePairCache = async (): Promise<void> => {
    const fp = fingerprintHash.trim();
    if (!fp) {
      conflictPairCache = [];
      conflictPairFp = "";
      return;
    }
    if (conflictPairFp === fp && conflictPairCache.length) return;
    try {
      const out = await improvementPoints.conflictPairs(fp, false);
      conflictPairCache = out.pairs || [];
      conflictPairFp = fp;
    } catch {
      conflictPairCache = [];
      conflictPairFp = fp;
    }
  };

  const openAggregateGroup = (
    members: ImprovementPoint[],
    gid: string,
    clusterIndex: number,
  ) => {
    if (!fingerprintHash) {
      window.alert("请先选择带书标识的导入任务。");
      return;
    }
    const ids = members.map((m) => String(m.id || "")).filter(Boolean);
    if (ids.length < 2) {
      window.alert("该簇不足 2 条，无法聚合裁决。");
      return;
    }
    const clusterLabel = `簇#${clusterIndex}`;
    const reason =
      aggregateReasonOf(members[0]!) ||
      conflictPairCache.find(
        (p) =>
          ids.includes(String(p.a_id || "")) &&
          ids.includes(String(p.b_id || "")),
      )?.reason ||
      "";
    const pages = [
      ...new Set(members.map((m) => Number(m.page_no) || 0).filter((n) => n > 0)),
    ].sort((a, b) => a - b);
    void openImprovementsCopilot({
      intent: "aggregate",
      payload: {
        fingerprint_hash: fingerprintHash,
        job_id: jobId || undefined,
        job_label: jobLabel || undefined,
        focus: "aggregate",
        process_all: false,
        aggregate_group_id: gid || undefined,
        aggregate_cluster_index: clusterIndex,
        aggregate_cluster_label: clusterLabel,
        member_ids: ids,
        user_goal:
          `只处理${clusterLabel}（${ids.length} 条` +
          (pages.length ? `，页 ${pages.join("/")}` : "") +
          (reason ? `，${reason}` : "") +
          "）。禁止其它簇；不要问全部还是一个。确认后 aggregate_resolve。",
      },
    }).catch((e) => window.alert(`启动${clusterLabel}聚合失败：${e}`));
  };

  const header = el("div", "edition-ops-header improvements-header");
  const titleRow = el("div", "improvements-header-row");
  const title = el("h2", "edition-ops-title", "改进点");
  const reloadBtn = el("button", "btn-secondary improvements-reload", "刷新") as HTMLButtonElement;
  reloadBtn.type = "button";
  reloadBtn.title = "重新加载本书改进台账";
  titleRow.append(title, reloadBtn);
  const bookLine = el(
    "p",
    "muted edition-ops-lead improvements-book-line",
    "左侧选择导入任务，查看这本书的改进台账",
  );
  const hintEl = el("p", "improvements-hint", "");
  header.append(titleRow, bookLine, hintEl);
  root.appendChild(header);

  const viewToggle = el("div", "improvements-view-toggle");
  viewToggle.setAttribute("role", "tablist");
  viewToggle.setAttribute("aria-label", "改进点、抽象结果、检测器与词表");
  const viewIpsBtn = el(
    "button",
    "improvements-view-btn active",
    "改进点",
  ) as HTMLButtonElement;
  viewIpsBtn.type = "button";
  viewIpsBtn.title = "本书改进点台账：整理、拆分、聚合";
  const viewPatBtn = el(
    "button",
    "improvements-view-btn",
    "抽象结果",
  ) as HTMLButtonElement;
  viewPatBtn.type = "button";
  viewPatBtn.title = "全库已凝练的可复用模式（症状 · 检测器 · 手册）";
  const viewDetBtn = el(
    "button",
    "improvements-view-btn",
    "检测器",
  ) as HTMLButtonElement;
  viewDetBtn.type = "button";
  viewDetBtn.title = "跨书症状检测器，以及每条用到的配置词 / 词插件";
  const viewVocabBtn = el(
    "button",
    "improvements-view-btn",
    "词表",
  ) as HTMLButtonElement;
  viewVocabBtn.type = "button";
  viewVocabBtn.title = "L2 配置词、L2b 词插件、派生特征，以及谁在用它们";
  viewToggle.append(viewIpsBtn, viewPatBtn, viewDetBtn, viewVocabBtn);
  root.appendChild(viewToggle);

  const prepStepper = el("div", "improvements-prep-stepper");
  prepStepper.setAttribute("aria-label", "整理进度");
  root.appendChild(prepStepper);

  const conflictBanner = el("div", "improvements-conflict-banner");
  conflictBanner.hidden = true;
  root.appendChild(conflictBanner);

  const filterCard = el("div", "improvements-filter-card");
  root.appendChild(filterCard);

  const patternFilterCard = el("div", "improvements-filter-card improvements-pattern-filters");
  patternFilterCard.hidden = true;
  root.appendChild(patternFilterCard);

  const detectorFilterCard = el("div", "improvements-filter-card improvements-detector-filters");
  detectorFilterCard.hidden = true;
  root.appendChild(detectorFilterCard);

  const vocabFilterCard = el("div", "improvements-filter-card improvements-vocab-filters");
  vocabFilterCard.hidden = true;
  root.appendChild(vocabFilterCard);

  const statusBtns = new Map<StatusFilter, HTMLButtonElement>();
  const statusDefs: Array<{ id: StatusFilter; label: string }> = [];

  /** 多选芯片行：activeIds 空 ⇔「全部」高亮；点选项切换集合 */
  const makeMultiChipBlock = (
    label: string,
  ): {
    block: HTMLElement;
    setOptions: (
      opts: Array<{ id: string; label: string; count?: number }>,
      activeIds: Set<string>,
      onToggle: (id: string) => void,
    ) => void;
  } => {
    const block = el("div", "improvements-chip-block");
    block.appendChild(el("span", "muted improvements-chip-label", label));
    const row = el("div", "improvements-status-chips");
    block.appendChild(row);
    const setOptions = (
      opts: Array<{ id: string; label: string; count?: number }>,
      activeIds: Set<string>,
      onToggle: (id: string) => void,
    ) => {
      row.innerHTML = "";
      const none = activeIds.size === 0;
      for (const d of opts) {
        const text = d.count != null ? `${d.label} ${d.count}` : d.label;
        const b = el("button", "improvements-chip", text) as HTMLButtonElement;
        b.type = "button";
        const isAll = d.id === "";
        b.classList.toggle("active", isAll ? none : activeIds.has(d.id));
        b.title = isAll ? "清除本行筛选" : "点击复选/取消";
        b.onclick = () => onToggle(d.id);
        row.appendChild(b);
      }
    };
    return { block, setOptions };
  };

  const toggleInSet = (set: Set<string>, id: string) => {
    if (!id) {
      set.clear();
      return;
    }
    if (set.has(id)) set.delete(id);
    else set.add(id);
  };

  const prepBlock = el("div", "improvements-chip-block");
  prepBlock.appendChild(el("span", "muted improvements-chip-label", "整理状态"));
  const prepRow = el("div", "improvements-status-chips");
  prepBlock.appendChild(prepRow);
  filterCard.appendChild(prepBlock);

  const syncPrepChips = () => {
    prepRow.innerHTML = "";
    const bp = lastData?.counts?.by_prep_status || {};
    const defs: Array<{ id: PrepFilter; label: string; count: number }> = [
      {
        id: "",
        label: "全部",
        count: Number(lastData?.counts?.total || 0),
      },
      {
        id: "needs_triage",
        label: "待整理",
        count: Number(bp.needs_triage || 0),
      },
      {
        id: "needs_split",
        label: "待拆分",
        count: Number(bp.needs_split || 0),
      },
      {
        id: "needs_aggregate",
        label: "需聚合",
        count: Number(bp.needs_aggregate || bp.needs_conflict_resolve || 0),
      },
      {
        id: "ready",
        label: "完成体",
        count: Number(bp.ready || 0),
      },
    ];
    for (const d of defs) {
      const b = el(
        "button",
        "improvements-chip",
        `${d.label} ${d.count}`,
      ) as HTMLButtonElement;
      b.type = "button";
      b.classList.toggle("active", prepFilter === d.id);
      b.onclick = () => {
        prepFilter = d.id;
        syncPrepChips();
        paintFromCache();
      };
      prepRow.appendChild(b);
    }
  };

  const originBlock = makeMultiChipBlock("来源");
  const catBlock = makeMultiChipBlock("类别");
  filterCard.appendChild(originBlock.block);
  filterCard.appendChild(catBlock.block);

  const syncOriginChips = () => {
    const items = lastData?.items || [];
    const ctr = new Map<string, number>();
    for (const it of items) {
      const lab = originLabel(it);
      ctr.set(lab, (ctr.get(lab) || 0) + 1);
    }
    const valid = new Set(ctr.keys());
    for (const id of [...originSelected]) {
      if (!valid.has(id)) originSelected.delete(id);
    }
    const opts: Array<{ id: string; label: string; count?: number }> = [
      { id: "", label: "全部", count: items.length },
      ...[...ctr.entries()]
        .sort((a, b) => a[0].localeCompare(b[0], "zh"))
        .map(([lab, n]) => ({ id: lab, label: lab, count: n })),
    ];
    originBlock.setOptions(opts, originSelected, (id) => {
      toggleInSet(originSelected, id);
      syncOriginChips();
      paintFromCache();
    });
  };

  const syncCatChips = () => {
    const items = lastData?.items || [];
    const opts: Array<{ id: string; label: string; count?: number }> = [
      { id: "", label: "全部", count: items.length },
    ];
    const valid = new Set<string>();
    for (const [k, lab] of Object.entries(CAT_LABEL)) {
      const n = items.filter(
        (it) => String(it.category || "").toLowerCase() === k,
      ).length;
      if (n > 0) {
        opts.push({ id: k, label: lab, count: n });
        valid.add(k);
      }
    }
    for (const id of [...catSelected]) {
      if (!valid.has(id)) catSelected.delete(id);
    }
    catBlock.setOptions(opts, catSelected, (id) => {
      toggleInSet(catSelected, id);
      syncCatChips();
      paintFromCache();
    });
  };

  // —— 排列方式：单选 ——
  const arrangeBlock = el("div", "improvements-chip-block");
  arrangeBlock.appendChild(el("span", "muted improvements-chip-label", "排列"));
  const arrangeRow = el("div", "improvements-status-chips");
  arrangeBlock.appendChild(arrangeRow);
  const syncArrangeChips = () => {
    arrangeRow.innerHTML = "";
    const defs: Array<{ id: ArrangeMode; label: string; hint: string }> = [
      { id: "list", label: "列表", hint: "一条改进一行" },
      {
        id: "by_aggregate",
        label: "按需聚合",
        hint: "需聚合按簇#1、簇#2…分组；标题旁「处理聚合」只处理该簇",
      },
      {
        id: "by_dialogue",
        label: "按对话聚合",
        hint: "同一句用户要求派生出的多条改进折叠成一组",
      },
      { id: "by_problem", label: "按问题聚合", hint: "同一页同一处的改进归到一起" },
      { id: "by_pattern", label: "按可复用规则", hint: "按可复用规则草案分组" },
    ];
    for (const d of defs) {
      const b = el("button", "improvements-chip", d.label) as HTMLButtonElement;
      b.type = "button";
      b.title = d.hint;
      b.classList.toggle("active", arrange === d.id);
      b.onclick = () => {
        arrange = d.id;
        if (d.id !== "by_aggregate") focusClusterKey = "";
        // 切到「按需聚合」时自动筛到需聚合，避免和完成体混在一组里
        if (d.id === "by_aggregate") {
          prepFilter = "needs_aggregate";
          syncPrepChips();
          void ensureAggregatePairCache();
        }
        syncArrangeChips();
        paintFromCache();
      };
      arrangeRow.appendChild(b);
    }
  };
  filterCard.appendChild(arrangeBlock);

  // —— 仅页码 / 关键词仍用输入（不适合芯片） ——
  const filters = el("div", "edition-ops-toolbar improvements-filters improvements-col-filters");
  const pageInput = el("input", "edition-ops-input") as HTMLInputElement;
  pageInput.type = "number";
  pageInput.min = "1";
  pageInput.placeholder = "页码";
  pageInput.title = "按页码筛选";
  pageInput.style.width = "5rem";
  const kwInput = el("input", "edition-ops-input") as HTMLInputElement;
  kwInput.placeholder = "摘要关键词";
  kwInput.title = "筛选摘要 / 位点";
  kwInput.style.maxWidth = "12rem";
  const clearFilters = el("button", "btn-link", "清空筛选") as HTMLButtonElement;
  clearFilters.type = "button";
  clearFilters.title = "清空整理/来源/类别/页/关键词";
  const triageBtn = el(
    "button",
    "btn-primary improvements-triage-btn",
    "整理",
  ) as HTMLButtonElement;
  triageBtn.type = "button";
  triageBtn.title =
    "用 Cursor Agent 清空本书全部待整理 → 完成体 / 待拆分 / 需聚合（写库后中栏会软刷）";
  triageBtn.onclick = () => {
    if (!fingerprintHash) {
      window.alert("请先选择带书标识的导入任务。");
      return;
    }
    void openImprovementsCopilot({
      intent: "triage",
      payload: {
        fingerprint_hash: fingerprintHash,
        job_id: jobId || undefined,
        job_label: jobLabel || undefined,
        process_all: true, /* process_all-v1 */
        limit: 2000,
        user_goal:
          "处理该书全部待整理改进点，直到 needs_triage 为 0；每批 triage_apply 后继续 list。",
      },
    }).catch((e) => window.alert(`启动整理失败：${e}`));
  };
  const splitAllBtn = el(
    "button",
    "btn-secondary improvements-split-all-btn",
    "全部拆分",
  ) as HTMLButtonElement;
  splitAllBtn.type = "button";
  splitAllBtn.title =
    "全书入口：对全部待拆分一次性 AI 拆分（与行内「拆分本条」不同）";
  splitAllBtn.onclick = () => {
    if (!fingerprintHash) {
      window.alert("请先选择带书标识的导入任务。");
      return;
    }
    const n = Number(lastData?.counts?.by_prep_status?.needs_split || 0);
    if (n <= 0) {
      window.alert("当前没有待拆分条目。请先「整理」标出待拆分。");
      return;
    }
    void openImprovementsCopilot({
      intent: "split_all",
      payload: {
        fingerprint_hash: fingerprintHash,
        job_id: jobId || undefined,
        job_label: jobLabel || undefined,
        process_all: true,
        limit: 2000,
        user_goal:
          "对该书全部 needs_split 逐条完备路径拆分并写库，直到 remaining_needs_split=0。",
      },
    }).catch((e) => window.alert(`启动全部拆分失败：${e}`));
  };
  const detectConflictBtn = el(
    "button",
    "btn-secondary improvements-detect-conflict",
    "处理全部聚合",
  ) as HTMLButtonElement;
  detectConflictBtn.type = "button";
  detectConflictBtn.title =
    "全书：按簇#1、簇#2…处理全部对立簇（中栏点簇标题则只处理那一簇）";
  detectConflictBtn.onclick = () => {
    if (!fingerprintHash) {
      window.alert("请先选择带书标识的导入任务。");
      return;
    }
    void openImprovementsCopilot({
      intent: "aggregate",
      payload: {
        fingerprint_hash: fingerprintHash,
        job_id: jobId || undefined,
        job_label: jobLabel || undefined,
        focus: "aggregate",
        process_all: true,
        user_goal:
          "处理该书全部需聚合簇（簇#1、簇#2…），逐簇成对说明并经确认后写库。",
      },
    }).catch((e) => window.alert(`启动全部聚合失败：${e}`));
  };
  const abstractBtn = el(
    "button",
    "btn-secondary improvements-abstract-btn",
    "抽象成模式",
  ) as HTMLButtonElement;
  abstractBtn.type = "button";
  abstractBtn.title =
    "把本书完成体改进点抽象成跨书模式：症状名 + 检测器 YAML + 处置档位（下一本书导入即可复用）";
  abstractBtn.onclick = () => {
    if (!fingerprintHash) {
      window.alert("请先选择带书标识的导入任务。");
      return;
    }
    void openPatternAbstractCopilot({
      fingerprint_hash: fingerprintHash,
      job_id: jobId || undefined,
      job_label: jobLabel || undefined,
      batch_size: 25,
      user_goal:
        "把已完成体的改进点分批抽象成跨书模式：归症状、写检测器 YAML、配正反例、"
        + "自验召回与误报、定档记账；抽不出跨书症状的明确标为单书补丁。",
    }).catch((e) => window.alert(`启动模式抽象失败：${e}`));
  };
  const syncSplitAllBtn = (): void => {
    const n = Number(lastData?.counts?.by_prep_status?.needs_split || 0);
    splitAllBtn.disabled = !fingerprintHash || n <= 0;
    splitAllBtn.title =
      n > 0
        ? `全书：对 ${n} 条待拆分一次性 AI 拆分（与行内「拆分本条」不同）`
        : "当前没有待拆分条目";
  };
  filters.appendChild(el("span", "muted improvements-chip-label", "其它"));
  filters.appendChild(triageBtn);
  filters.appendChild(splitAllBtn);
  filters.appendChild(detectConflictBtn);
  filters.appendChild(abstractBtn);
  filters.appendChild(pageInput);
  filters.appendChild(kwInput);
  filters.appendChild(clearFilters);
  filterCard.appendChild(filters);

  const patChipBlock = el("div", "improvements-chip-block");
  patChipBlock.appendChild(el("span", "muted improvements-chip-label", "模式筛"));
  const patChipRow = el("div", "improvements-status-chips");
  patChipBlock.appendChild(patChipRow);
  patternFilterCard.appendChild(patChipBlock);
  const patKwRow = el("div", "edition-ops-toolbar improvements-filters");
  const patKwInput = el("input", "edition-ops-input") as HTMLInputElement;
  patKwInput.placeholder = "症状名 / 摘要 / 检测器";
  patKwInput.title = "筛选症状名、摘要或检测器文件名";
  patKwInput.style.maxWidth = "16rem";
  const patReloadBtn = el("button", "btn-secondary", "刷新模式") as HTMLButtonElement;
  patReloadBtn.type = "button";
  patReloadBtn.title = "重新拉取全库有成员的模式";
  patKwRow.append(patKwInput, patReloadBtn);
  patternFilterCard.appendChild(patKwRow);

  const detKwRow = el("div", "edition-ops-toolbar improvements-filters");
  const detKwInput = el("input", "edition-ops-input") as HTMLInputElement;
  detKwInput.placeholder = "症状名 / 标题 / 配置词 / 插件";
  detKwInput.title = "筛选检测器或它用到的挂载层词";
  detKwInput.style.maxWidth = "18rem";
  const detReloadBtn = el("button", "btn-secondary", "刷新检测器") as HTMLButtonElement;
  detReloadBtn.type = "button";
  detReloadBtn.title = "重新读 configs/checks（改完 YAML 点这里即可）";
  detKwRow.append(detKwInput, detReloadBtn);
  detectorFilterCard.appendChild(detKwRow);

  const vocabChipBlock = el("div", "improvements-chip-block");
  vocabChipBlock.appendChild(el("span", "muted improvements-chip-label", "层级"));
  const vocabChipRow = el("div", "improvements-status-chips");
  vocabChipBlock.appendChild(vocabChipRow);
  vocabFilterCard.appendChild(vocabChipBlock);
  const vocabKwRow = el("div", "edition-ops-toolbar improvements-filters");
  const vocabKwInput = el("input", "edition-ops-input") as HTMLInputElement;
  vocabKwInput.placeholder = "词名 / 释义 / 检测器";
  vocabKwInput.title = "筛选配置词、词插件、派生特征或用到它的检测器";
  vocabKwInput.style.maxWidth = "18rem";
  const vocabReloadBtn = el("button", "btn-secondary", "刷新词表") as HTMLButtonElement;
  vocabReloadBtn.type = "button";
  vocabReloadBtn.title = "重新读 configs/checks（改完 YAML 点这里即可）";
  vocabKwRow.append(vocabKwInput, vocabReloadBtn);
  vocabFilterCard.appendChild(vocabKwRow);

  const refreshColFilterOptions = () => {
    syncOriginChips();
    syncCatChips();
  };

  const statsEl = el("p", "muted improvements-stats", "");
  root.appendChild(statsEl);
  const sortHint = el(
    "p",
    "muted improvements-sort-hint",
    "双击表头排序 · 再双击同列切换升/降序",
  );
  root.appendChild(sortHint);
  const listHost = el("div", "improvements-list");
  root.appendChild(listHost);

  const publishContext = () => {
    document.dispatchEvent(
      new CustomEvent("studio:improvements-context", {
        detail: {
          jobId,
          fingerprintHash,
          jobLabel,
          selectedIds: [...selected],
          counts: lastData?.counts || null,
          items: lastData?.items || [],
        },
      }),
    );
  };

  const paintFocusUi = () => {
    listHost.querySelectorAll(".improvements-tr.is-focused").forEach((n) => {
      n.classList.remove("is-focused");
    });
    if (focusId) {
      for (const row of listHost.querySelectorAll(".improvements-tr")) {
        if ((row as HTMLElement).dataset.ipId === focusId) {
          row.classList.add("is-focused");
          const wrap = row.closest(
            ".improvements-table-wrap",
          ) as HTMLElement | null;
          const head = wrap?.previousElementSibling as HTMLElement | null;
          if (head?.classList.contains("improvements-agg-group-head")) {
            focusClusterKey = head.dataset.aggGid || focusClusterKey;
          }
          break;
        }
      }
    }
    listHost
      .querySelectorAll(".improvements-agg-group-head.is-focused")
      .forEach((n) => n.classList.remove("is-focused"));
    if (focusClusterKey) {
      const head = listHost.querySelector(
        `.improvements-agg-group-head[data-agg-gid="${CSS.escape(focusClusterKey)}"]`,
      );
      head?.classList.add("is-focused");
    }
  };

  const selectFocus = (it: ImprovementPoint | null) => {
    focusId = it ? String(it.id || "") : "";
    if (!it) {
      /* 保留 focusClusterKey：点簇标题清行但仍高亮该簇 */
    }
    document.dispatchEvent(
      new CustomEvent("studio:improvements-select", {
        detail: { item: it },
      }),
    );
    paintFocusUi();
  };

  const selectCluster = (gid: string) => {
    focusClusterKey = gid;
    focusId = "";
    document.dispatchEvent(
      new CustomEvent("studio:improvements-select", {
        detail: { item: null },
      }),
    );
    paintFocusUi();
  };

  const syncArrange = () => syncArrangeChips();
  syncArrange();

  const syncBookLine = () => {
    if (jobLabel || jobId || fingerprintHash) {
      const parts = ["本书改进台账"];
      if (jobLabel || jobId) parts.push(jobLabel || `任务 ${jobId.slice(0, 8)}…`);
      bookLine.textContent = parts.join(" · ");
    } else {
      bookLine.textContent = "左侧选择导入任务后，这里显示这本书的全部改进记录";
    }
  };

  const passFilters = (it: ImprovementPoint): boolean => {
    // 只展示成功改进点（进表即有效）
    const st = String(it.status || "").toLowerCase();
    if (st && st !== "accepted" && st !== "effective") return false;
    if (prepFilter && prepOf(it) !== prepFilter) return false;
    if (originSelected.size && !originSelected.has(originLabel(it))) {
      return false;
    }
    if (
      catSelected.size &&
      !catSelected.has(String(it.category || "").toLowerCase())
    ) {
      return false;
    }
    if (pageFilter) {
      const want = Number(pageFilter);
      if (!Number.isNaN(want) && Number(it.page_no) !== want) return false;
    }
    if (keyword) {
      const blob = `${claimSummary(it)} ${it.locus_key || ""}`.toLowerCase();
      if (!blob.includes(keyword.toLowerCase())) return false;
    }
    return true;
  };

  const paintPrepStepper = () => {
    prepStepper.innerHTML = "";
    const bp = lastData?.counts?.by_prep_status || {};
    const steps: Array<{ id: PrepStatus; n: number; step: string }> = [
      { id: "needs_triage", n: Number(bp.needs_triage || 0), step: "①" },
      { id: "needs_split", n: Number(bp.needs_split || 0), step: "②" },
      {
        id: "needs_aggregate",
        n: Number(bp.needs_aggregate || bp.needs_conflict_resolve || 0),
        step: "③",
      },
      { id: "ready", n: Number(bp.ready || 0), step: "④" },
    ];
    triageBtn.disabled = !fingerprintHash;
    triageBtn.classList.toggle("has-work", Number(bp.needs_triage || 0) > 0);
    syncSplitAllBtn();
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i]!;
      const cell = el("button", "improvements-prep-step") as HTMLButtonElement;
      cell.type = "button";
      cell.classList.toggle("is-active", prepFilter === s.id);
      cell.classList.toggle("has-work", s.n > 0 && s.id !== "ready");
      cell.innerHTML = `<span class="improvements-prep-step-n">${s.step}</span><span class="improvements-prep-step-lab">${PREP_STATUS_LABELS[s.id]}</span><span class="improvements-prep-step-count">${s.n}</span>`;
      cell.title = `筛选：${PREP_STATUS_LABELS[s.id]}`;
      cell.onclick = () => {
        prepFilter = prepFilter === s.id ? "" : s.id;
        syncPrepChips();
        paintFromCache();
      };
      prepStepper.appendChild(cell);
      if (i < steps.length - 1) {
        prepStepper.appendChild(el("span", "improvements-prep-arrow", "→"));
      }
    }
  };

  const updateStatusLabels = () => {
    const c = lastData?.counts;
    const bs = c?.by_status || {};
    const n = (k: StatusFilter): number => {
      if (!c) return 0;
      if (k === "all") return c.total || 0;
      if (k === "accepted") return c.accepted || 0;
      if (k === "conflict") return c.conflict || 0;
      if (k === "superseded") return c.superseded ?? bs.superseded ?? 0;
      if (k === "rejected") return c.rejected ?? bs.rejected ?? 0;
      if (k === "pending") return c.pending ?? 0;
      return 0;
    };
    for (const d of statusDefs) {
      const btn = statusBtns.get(d.id);
      if (btn) btn.textContent = `${d.label} ${n(d.id)}`;
    }
  };

  const paintConflictBanner = () => {
    // 改进点进表即有效，不再有「待裁决」状态
    conflictBanner.innerHTML = "";
    conflictBanner.hidden = true;
  };

  const renderTable = (items: ImprovementPoint[]) => {
    const sorted = sortItems(items, sortKey, sortDir);
    if (!sorted.length) {
      listHost.appendChild(
        el(
          "p",
          "muted improvements-empty",
          fingerprintHash || jobId
            ? "当前筛选下没有记录。"
            : "请先在左侧选择导入任务。",
        ),
      );
      return;
    }
    const wrap = el("div", "improvements-table-wrap");
    const table = el("table", "improvements-table");
    const thead = el("thead", "");
    const hr = el("tr", "");
    for (const h of SORT_HEADERS) {
      const th = el("th", "", h.label) as HTMLTableCellElement;
      if (h.kind === "sort" && h.key) {
        th.classList.add("improvements-th-sortable");
        th.title = "双击排序";
        if (sortKey === h.key) {
          th.classList.add("is-sorted", sortDir === "asc" ? "is-asc" : "is-desc");
          th.textContent = `${h.label}${sortDir === "asc" ? " ↑" : " ↓"}`;
        }
        th.ondblclick = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          if (sortKey === h.key) {
            sortDir = sortDir === "asc" ? "desc" : "asc";
          } else {
            sortKey = h.key;
            sortDir = "asc";
          }
          paintFromCache();
        };
      } else if (h.kind === "check") {
        th.classList.add("improvements-th-check");
      } else if (h.kind === "prep") {
        th.classList.add("improvements-th-prep");
        th.title = "整理状态：待整理 / 待拆分 / 需聚合 / 完成体";
      } else {
        th.classList.add("improvements-th-action");
        th.title = "拆分 / 处理聚合 / 删除";
      }
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody", "");

    for (const it of sorted) {
      const id = String(it.id || "");
      const tr = el("tr", "improvements-tr");
      tr.dataset.ipId = id;
      if (id && id === focusId) tr.classList.add("is-focused");
      const st = String(it.status || "").toLowerCase();
      tr.classList.add("is-accepted");
      tr.title = "点击在右侧查看详情与操作";
      tr.onclick = (ev) => {
        const t = ev.target as HTMLElement | null;
        if (t && (t.closest("input") || t.closest("button"))) return;
        selectFocus(it);
      };

      const tdCheck = el("td", "improvements-td-check");
      const cb = el("input", "") as HTMLInputElement;
      cb.type = "checkbox";
      cb.checked = selected.has(id);
      cb.disabled = st !== "accepted" && st !== "effective";
      cb.title = "勾选后可在右栏批量设置可复用规则";
      cb.onclick = (ev) => ev.stopPropagation();
      cb.onchange = () => {
        if (cb.checked) selected.add(id);
        else selected.delete(id);
        publishContext();
        statsEl.textContent =
          statsEl.textContent?.replace(
            /已选 \d+ 条/,
            `已选 ${selected.size} 条`,
          ) || statsEl.textContent;
      };
      tdCheck.appendChild(cb);
      tr.appendChild(tdCheck);

      tr.appendChild(el("td", "improvements-td-page", `p${it.page_no ?? "?"}`));

      const originLab = originLabel(it);
      const tdOrigin = el("td", "improvements-td-origin", originLab);
      tdOrigin.title =
        originLab === "AI 自主改"
          ? "全书导入时 AI 自主分析并改成功的"
          : "客户在对话里提出要求，AI 按要求改成功的";
      tr.appendChild(tdOrigin);

      tr.appendChild(
        el("td", "improvements-td-cat", catLabel(it.category)),
      );

      const prep = prepOf(it);
      const tdPrep = el("td", "improvements-td-prep");
      const badge = el(
        "span",
        `improvements-prep-badge improvements-prep-badge--${prep}`,
        prepLabel(it),
      );
      tdPrep.appendChild(badge);
      tr.appendChild(tdPrep);

      const sum = claimSummary(it);
      const tdSum = el(
        "td",
        "improvements-td-summary",
        sum.slice(0, 56) + (sum.length > 56 ? "…" : ""),
      );
      tdSum.title = sum;
      tr.appendChild(tdSum);

      const tdAct = el("td", "improvements-td-action");
      if (prep === "needs_split") {
        const splitBtn = el(
          "button",
          "improvements-row-cta improvements-row-cta--split",
          "拆分本条",
        ) as HTMLButtonElement;
        splitBtn.type = "button";
        splitBtn.title = "单条入口：打开拆分工作台（不是「全部拆分」）";
        splitBtn.onclick = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          selectFocus(it);
          document.dispatchEvent(
            new CustomEvent("studio:improvements-workbench", {
              detail: { mode: "split", item: it },
            }),
          );
        };
        tdAct.appendChild(splitBtn);
      } else if (prep === "needs_aggregate" && arrange !== "by_aggregate") {
        // 「按需聚合」排列下：簇标题旁有「处理聚合」，行内不再放按钮防误触
        const conflictBtn = el(
          "button",
          "improvements-row-cta improvements-row-cta--conflict",
          "处理聚合",
        ) as HTMLButtonElement;
        conflictBtn.type = "button";
        conflictBtn.title = "打开聚合工作台";
        conflictBtn.onclick = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          selectFocus(it);
          document.dispatchEvent(
            new CustomEvent("studio:improvements-workbench", {
              detail: { mode: "conflict", item: it },
            }),
          );
        };
        tdAct.appendChild(conflictBtn);
      }
      const delBtn = el("button", "improvements-del-btn", "删除") as HTMLButtonElement;
      delBtn.type = "button";
      delBtn.title = "彻底删除此改进点及专书页补丁等派生记录（悬停/选中本行时显示）";
      delBtn.onclick = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        void purgeOne(it);
      };
      tdAct.appendChild(delBtn);
      tr.appendChild(tdAct);

      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    listHost.appendChild(wrap);
  };

  const publishViewMode = () => {
    document.dispatchEvent(
      new CustomEvent("studio:improvements-view-mode", {
        detail: { viewMode },
      }),
    );
  };

  const selectPattern = (pat: ImprovementPattern | null) => {
    focusPatternId = pat ? String(pat.id || "") : "";
    document.dispatchEvent(
      new CustomEvent("studio:patterns-select", {
        detail: { pattern: pat },
      }),
    );
    listHost.querySelectorAll(".improvements-tr.is-focused").forEach((n) => {
      n.classList.remove("is-focused");
    });
    if (focusPatternId) {
      for (const row of listHost.querySelectorAll(".improvements-tr")) {
        if ((row as HTMLElement).dataset.patternId === focusPatternId) {
          row.classList.add("is-focused");
        }
      }
    }
  };

  const selectDetector = (suspect: string, word = "") => {
    focusSuspect = String(suspect || "").trim();
    focusWord = String(word || "").trim();
    document.dispatchEvent(
      new CustomEvent("studio:detectors-select", {
        detail: { suspectType: focusSuspect, highlightWord: focusWord },
      }),
    );
    listHost.querySelectorAll(".improvements-tr.is-focused").forEach((n) => {
      n.classList.remove("is-focused");
    });
    if (!focusSuspect) return;
    for (const row of listHost.querySelectorAll(".improvements-tr")) {
      const elRow = row as HTMLElement;
      if (elRow.dataset.suspect === focusSuspect) elRow.classList.add("is-focused");
    }
  };

  const selectVocab = (wordName: string, suspect = "") => {
    focusVocabWord = String(wordName || "").trim();
    focusSuspect = String(suspect || "").trim();
    document.dispatchEvent(
      new CustomEvent("studio:vocab-select", {
        detail: { wordName: focusVocabWord, suspectType: focusSuspect },
      }),
    );
    listHost.querySelectorAll(".improvements-tr.is-focused").forEach((n) => {
      n.classList.remove("is-focused");
    });
    if (!focusVocabWord) return;
    for (const row of listHost.querySelectorAll(".improvements-tr")) {
      const elRow = row as HTMLElement;
      if (elRow.dataset.word === focusVocabWord) elRow.classList.add("is-focused");
    }
  };

  const passPatternFilters = (p: ImprovementPattern): boolean => {
    const det = String(p.detector_ref || "").trim();
    const kind = String(p.remediation_kind || "").trim().toLowerCase();
    if (patternChip === "has_detector" && !det) return false;
    if (patternChip === "no_detector" && det) return false;
    if (patternChip === "playbook" && kind !== "playbook") return false;
    if (patternChip === "rule" && kind !== "rule") return false;
    const kw = patternKeyword.trim().toLowerCase();
    if (!kw) return true;
    const hay = [
      p.suspect_type,
      p.abstract_text,
      p.detector_ref,
      p.playbook,
      p.category,
      p.id,
    ]
      .map((x) => String(x || "").toLowerCase())
      .join("\n");
    return hay.includes(kw);
  };

  const syncPatternChips = () => {
    patChipRow.innerHTML = "";
    const defs: Array<{ id: PatternChipFilter; label: string }> = [
      { id: "all", label: "全部" },
      { id: "has_detector", label: "有检测器" },
      { id: "no_detector", label: "仅手册无检测器" },
      { id: "playbook", label: "playbook" },
      { id: "rule", label: "rule" },
    ];
    for (const d of defs) {
      const b = el("button", "improvements-chip", d.label) as HTMLButtonElement;
      b.type = "button";
      b.classList.toggle("active", patternChip === d.id);
      b.onclick = () => {
        patternChip = d.id;
        syncPatternChips();
        paintPatternsCatalog();
      };
      patChipRow.appendChild(b);
    }
  };

  const paintPatternsCatalog = () => {
    listHost.innerHTML = "";
    sortHint.hidden = true;
    if (patternsBusy && !patternsLoaded) {
      listHost.appendChild(el("p", "muted", "正在加载抽象结果…"));
      return;
    }
    const rows = lastPatterns.filter(passPatternFilters).sort((a, b) => {
      const mc = Number(b.member_count || 0) - Number(a.member_count || 0);
      if (mc) return mc;
      return Number(b.book_count || 0) - Number(a.book_count || 0);
    });
    const withDet = lastPatterns.filter((p) => String(p.detector_ref || "").trim()).length;
    const membersSum = lastPatterns.reduce((n, p) => n + Number(p.member_count || 0), 0);
    const cross2 = lastPatterns.filter((p) => Number(p.book_count || 0) >= 2).length;
    hintEl.textContent =
      "以模式表为真相源：症状名 + 检测器 + 处置（playbook/rule）+ 手册。点选一行看右栏详情与晋级门。";
    statsEl.textContent =
      `模式 ${lastPatterns.length} · 有检测器 ${withDet} · 成员合计 ${membersSum}` +
      ` · 跨书≥2 的 ${cross2}` +
      (rows.length !== lastPatterns.length ? ` · 当前筛选 ${rows.length}` : "");
    if (!rows.length) {
      listHost.appendChild(
        el(
          "p",
          "muted improvements-empty",
          patternsLoaded
            ? "没有符合筛选的模式。可点「抽象成模式」凝练新改进点，或清空筛选。"
            : "尚未加载模式列表。",
        ),
      );
      return;
    }
    const wrap = el("div", "improvements-table-wrap");
    const table = el("table", "improvements-table improvements-pattern-table");
    const thead = el("thead");
    const hr = el("tr");
    for (const lab of [
      "症状名",
      "摘要",
      "检测器",
      "处置",
      "成员",
      "跨书",
      "检测命中/误报",
      "状态",
      "生效面",
      "生效面描述",
    ]) {
      hr.appendChild(el("th", "", lab));
    }
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");
    for (const p of rows) {
      const tr = el("tr", "improvements-tr");
      tr.dataset.patternId = String(p.id || "");
      if (focusPatternId && focusPatternId === p.id) tr.classList.add("is-focused");
      const det = String(p.detector_ref || "").trim();
      const kind = String(p.remediation_kind || "").trim() || "—";
      const landingCode = String(p.landing_target || "").trim() || "未定";
      const landingDesc =
        String(p.landing_target_label || "").trim() ||
        (p.landing_target
          ? LANDING_TARGETS[p.landing_target as keyof typeof LANDING_TARGETS] ||
            p.landing_target
          : "未定");
      const abs = String(p.abstract_text || "").trim();
      const statusRaw = String(p.status || "").trim();
      const statusLabel = patternStatusLabel(statusRaw);
      const cells = [
        String(p.suspect_type || "").trim() || "（未命名）",
        abs.length > 48 ? `${abs.slice(0, 48)}…` : abs || "—",
        det || "无",
        kind,
        String(p.member_count ?? 0),
        String(p.book_count ?? 0),
        `${p.hits ?? 0}/${p.false_positive ?? 0}`,
        statusLabel,
        landingCode,
        landingDesc,
      ];
      for (const c of cells) tr.appendChild(el("td", "", c));
      tr.title =
        [
          abs || String(p.suspect_type || p.id),
          statusRaw ? `状态码：${statusRaw}` : "",
        ]
          .filter(Boolean)
          .join("\n");
      tr.onclick = () => selectPattern(p);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    listHost.appendChild(wrap);
  };

  const loadPatterns = async (opts: { force?: boolean } = {}): Promise<void> => {
    if (patternsBusy) return;
    if (patternsLoaded && !opts.force) {
      paintPatternsCatalog();
      return;
    }
    patternsBusy = true;
    paintPatternsCatalog();
    try {
      const out = await patterns.list({
        min_members: 1,
        with_members: true,
        limit: 500,
      });
      lastPatterns = out.patterns || [];
      patternsLoaded = true;
      if (
        focusPatternId &&
        !lastPatterns.some((p) => String(p.id) === focusPatternId)
      ) {
        selectPattern(null);
      } else if (focusPatternId) {
        const cur = lastPatterns.find((p) => String(p.id) === focusPatternId) || null;
        if (cur) selectPattern(cur);
      }
    } catch (err) {
      lastPatterns = [];
      patternsLoaded = true;
      listHost.innerHTML = "";
      listHost.appendChild(
        el("p", "error", `加载抽象结果失败：${String(err)}`),
      );
      statsEl.textContent = "";
      return;
    } finally {
      patternsBusy = false;
    }
    paintPatternsCatalog();
  };

  const passDetectorFilters = (c: SymptomCheckInfo, ov: ChecksOverview): boolean => {
    const kw = detectorKeyword.trim().toLowerCase();
    if (!kw) return true;
    const words = mountedWordsUsedBy(c, ov);
    const hay = [
      c.suspect_type,
      c.title,
      c.source,
      c.when,
      ...words.map((w) => w.word.name),
      ...words.map((w) => w.word.doc),
    ]
      .map((x) => String(x || "").toLowerCase())
      .join(" ");
    return hay.includes(kw);
  };

  const paintDetectorsCatalog = () => {
    listHost.innerHTML = "";
    sortHint.hidden = true;
    if (checksBusy && !checksLoaded) {
      listHost.appendChild(el("p", "muted", "正在读取检测器声明…"));
      return;
    }
    if (checksError) {
      listHost.appendChild(el("p", "error", checksError));
      return;
    }
    const ov = lastChecks;
    if (!ov) {
      listHost.appendChild(el("p", "muted", "尚未读取检测器。"));
      return;
    }
    const checks = (ov.checks || []).filter((c) => passDetectorFilters(c, ov));
    const all = ov.checks || [];
    const loadErrs = ov.load_errors || [];
    const orphans = ov.vocabulary?.diagnostics?.unused_hot || [];
    hintEl.textContent =
      "每条检测器下面挂它 when 里用到的配置词 / 词插件 / 派生特征。一对多如实画，共用词标出来。";
    statsEl.textContent =
      `检测器 ${all.length} 条` +
      (checks.length !== all.length ? ` · 当前筛选 ${checks.length}` : "") +
      (loadErrs.length ? ` · 加载失败 ${loadErrs.length}` : "") +
      (orphans.length ? ` · 孤词 ${orphans.length}` : "");

    if (!checks.length && !loadErrs.length && !orphans.length) {
      listHost.appendChild(
        el(
          "p",
          "muted improvements-empty",
          checksLoaded ? "没有符合筛选的检测器。" : "尚未加载检测器列表。",
        ),
      );
      return;
    }

    const wrap = el("div", "improvements-table-wrap");
    const table = el("table", "improvements-table improvements-detector-table");
    const thead = el("thead");
    const hr = el("tr");
    for (const lab of ["症状名", "说明", "范围", "处置", "状态"]) {
      hr.appendChild(el("th", "", lab));
    }
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");

    for (const c of checks) {
      const suspect = String(c.suspect_type || "").trim();
      const words = mountedWordsUsedBy(c, ov);
      const live = c.enabled === false ? "已停用" : "已加载";
      const tr = el("tr", "improvements-tr improvements-detector-main");
      tr.dataset.suspect = suspect;
      if (focusSuspect && focusSuspect === suspect) tr.classList.add("is-focused");
      const cells = [
        suspect || "（未命名）",
        String(c.title || "").trim() || "—",
        SCOPE_LABEL_SHORT[c.scope || ""] || c.scope || "—",
        String(c.remediation_kind || "").trim() || "—",
        live,
      ];
      for (const [i, text] of cells.entries()) {
        const td = el("td", i === 0 ? "improvements-insp-mono" : "", text);
        if (i === 4 && c.enabled === false) td.classList.add("improvements-detector-bad");
        tr.appendChild(td);
      }
      tr.onclick = () => selectDetector(suspect);
      tbody.appendChild(tr);

      if (!words.length) {
        const empty = el("tr", "improvements-detector-word");
        empty.dataset.suspect = suspect;
        const td = el("td", "muted", "└ 判据里没有挂载层词（只用了 L0 / L1）");
        td.colSpan = 5;
        empty.appendChild(td);
        empty.onclick = () => selectDetector(suspect);
        tbody.appendChild(empty);
        continue;
      }
      for (const use of words) {
        const wr = el("tr", "improvements-detector-word");
        wr.dataset.suspect = suspect;
        wr.dataset.word = use.word.name;
        if (focusSuspect === suspect && focusWord === use.word.name) {
          wr.classList.add("is-focused");
        }
        const nameTd = el("td", "improvements-insp-mono");
        nameTd.appendChild(el("span", "muted", "└ "));
        nameTd.appendChild(document.createTextNode(use.word.name));
        const layerTd = el("td", "", LAYER_LABEL[use.word.layer] || use.word.layer);
        const shareTd = el(
          "td",
          use.exclusive ? "" : "improvements-detector-shared",
          shareBadge(use),
        );
        const docTd = el("td", "muted", String(use.word.doc || "").slice(0, 48) || "—");
        docTd.colSpan = 2;
        wr.append(nameTd, layerTd, shareTd, docTd);
        wr.onclick = () => selectDetector(suspect, use.word.name);
        tbody.appendChild(wr);
      }
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    listHost.appendChild(wrap);

    if (orphans.length) {
      const foot = el("div", "improvements-detector-foot");
      foot.appendChild(
        el(
          "p",
          "improvements-detector-warn",
          `孤词 ${orphans.length} 个：${orphans.join("、")} —— 没有任何检测器会因为它们命中`,
        ),
      );
      listHost.appendChild(foot);
    }
    if (loadErrs.length) {
      const ul = el("ul", "improvements-detector-errors");
      for (const e of loadErrs) {
        const li = el("li");
        li.appendChild(el("code", "", e.file));
        li.appendChild(el("span", "", `：${(e.errors || []).join("；")}`));
        ul.appendChild(li);
      }
      listHost.appendChild(ul);
    }
  };

  const paintChecksCatalog = () => {
    if (viewMode === "vocab") paintVocabCatalog();
    else paintDetectorsCatalog();
  };

  const syncVocabChips = () => {
    vocabChipRow.innerHTML = "";
    const ov = lastChecks;
    const words = ov ? hotWords(ov) : [];
    const counts: Record<VocabChipFilter, number> = {
      all: words.length,
      L2: words.filter((w) => w.layer === "L2").length,
      L2b: words.filter((w) => w.layer === "L2b").length,
      derived: words.filter((w) => w.layer === "derived").length,
      unused: words.filter((w) => w.unused).length,
    };
    const defs: Array<{ id: VocabChipFilter; label: string }> = [
      { id: "all", label: "全部" },
      { id: "L2", label: "L2 配置词" },
      { id: "L2b", label: "L2b 词插件" },
      { id: "derived", label: "派生" },
      { id: "unused", label: "孤词" },
    ];
    for (const d of defs) {
      const b = el(
        "button",
        "improvements-chip",
        `${d.label} ${counts[d.id]}`,
      ) as HTMLButtonElement;
      b.type = "button";
      b.classList.toggle("active", vocabChip === d.id);
      b.onclick = () => {
        vocabChip = d.id;
        syncVocabChips();
        paintVocabCatalog();
      };
      vocabChipRow.appendChild(b);
    }
  };

  const passVocabFilters = (
    word: ReturnType<typeof hotWords>[number],
    ov: ChecksOverview,
  ): boolean => {
    if (vocabChip === "unused" && !word.unused) return false;
    if (vocabChip !== "all" && vocabChip !== "unused" && word.layer !== vocabChip) {
      return false;
    }
    const kw = vocabKeyword.trim().toLowerCase();
    if (!kw) return true;
    const users = (word.used_by || [])
      .map((f) => checkBySourceFile(ov, f)?.suspect_type || f)
      .join(" ");
    const hay = [word.name, word.doc, word.expr, word.source, users]
      .map((x) => String(x || "").toLowerCase())
      .join(" ");
    return hay.includes(kw);
  };

  const paintVocabCatalog = () => {
    listHost.innerHTML = "";
    sortHint.hidden = true;
    if (checksBusy && !checksLoaded) {
      listHost.appendChild(el("p", "muted", "正在读取词表…"));
      return;
    }
    if (checksError) {
      listHost.appendChild(el("p", "error", checksError));
      return;
    }
    const ov = lastChecks;
    if (!ov) {
      listHost.appendChild(el("p", "muted", "尚未读取词表。"));
      return;
    }
    const all = hotWords(ov);
    const rows = all.filter((w) => passVocabFilters(w, ov));
    const orphans = all.filter((w) => w.unused);
    hintEl.textContent =
      "以挂载层词为主：L2 配置词、L2b 词插件、派生特征。下面挂用到它的检测器。";
    statsEl.textContent =
      `词 ${all.length} 个` +
      ` · L2 ${all.filter((w) => w.layer === "L2").length}` +
      ` · L2b ${all.filter((w) => w.layer === "L2b").length}` +
      ` · 派生 ${all.filter((w) => w.layer === "derived").length}` +
      (rows.length !== all.length ? ` · 当前筛选 ${rows.length}` : "") +
      (orphans.length ? ` · 孤词 ${orphans.length}` : "");

    if (!rows.length) {
      listHost.appendChild(
        el(
          "p",
          "muted improvements-empty",
          checksLoaded ? "没有符合筛选的词。" : "尚未加载词表。",
        ),
      );
      return;
    }

    const wrap = el("div", "improvements-table-wrap");
    const table = el("table", "improvements-table improvements-detector-table");
    const thead = el("thead");
    const hr = el("tr");
    for (const lab of ["词名", "层级", "谁在用", "状态", "释义"]) {
      hr.appendChild(el("th", "", lab));
    }
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el("tbody");

    for (const word of rows) {
      const n = (word.used_by || []).length;
      const tr = el("tr", "improvements-tr improvements-detector-main");
      tr.dataset.word = word.name;
      if (focusVocabWord === word.name) tr.classList.add("is-focused");
      const status = word.unused ? "孤词" : n <= 1 ? "专用" : `被 ${n} 条共用`;
      const cells = [
        word.name,
        LAYER_LABEL[word.layer] || word.layer,
        n ? `${n} 条检测器` : "没有检测器",
        status,
        String(word.doc || "").slice(0, 48) || "—",
      ];
      for (const [i, text] of cells.entries()) {
        const td = el("td", i === 0 ? "improvements-insp-mono" : "", text);
        if (i === 3 && (word.unused || n > 1)) {
          td.classList.add(word.unused ? "improvements-detector-bad" : "improvements-detector-shared");
        }
        tr.appendChild(td);
      }
      tr.onclick = () => selectVocab(word.name);
      tbody.appendChild(tr);

      const users = word.used_by || [];
      if (!users.length) {
        const empty = el("tr", "improvements-detector-word");
        empty.dataset.word = word.name;
        const td = el("td", "muted", "└ 没有检测器在用（这只眼睛等于白开）");
        td.colSpan = 5;
        empty.appendChild(td);
        empty.onclick = () => selectVocab(word.name);
        tbody.appendChild(empty);
        continue;
      }
      for (const file of users) {
        const check = checkBySourceFile(ov, file);
        const suspect = String(check?.suspect_type || "").trim();
        const wr = el("tr", "improvements-detector-word");
        wr.dataset.word = word.name;
        if (suspect) wr.dataset.suspect = suspect;
        if (focusVocabWord === word.name && focusSuspect === suspect) {
          wr.classList.add("is-focused");
        }
        const nameTd = el("td", "improvements-insp-mono");
        nameTd.appendChild(el("span", "muted", "└ "));
        nameTd.appendChild(document.createTextNode(suspect || file));
        const titleTd = el("td", "", String(check?.title || "").trim() || "—");
        const kindTd = el("td", "", String(check?.remediation_kind || "").trim() || "—");
        const liveTd = el(
          "td",
          check?.enabled === false ? "improvements-detector-bad" : "",
          check ? (check.enabled === false ? "已停用" : "已加载") : "声明找不到",
        );
        const fileTd = el("td", "muted", file);
        wr.append(nameTd, titleTd, kindTd, liveTd, fileTd);
        wr.onclick = () => selectVocab(word.name, suspect);
        tbody.appendChild(wr);
      }
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    listHost.appendChild(wrap);
  };

  const loadChecks = async (opts: { force?: boolean } = {}) => {
    if (checksBusy) return;
    if (checksLoaded && !opts.force) {
      paintChecksCatalog();
      return;
    }
    checksBusy = true;
    checksError = "";
    paintChecksCatalog();
    try {
      lastChecks = await corpus.checksOverview();
      checksLoaded = true;
    } catch (err) {
      lastChecks = null;
      checksLoaded = true;
      checksError = `读取检测器失败：${String(err)}`;
    } finally {
      checksBusy = false;
    }
    if (viewMode === "vocab") syncVocabChips();
    paintChecksCatalog();
    if (viewMode === "vocab" && focusVocabWord) selectVocab(focusVocabWord, focusSuspect);
    else if (viewMode === "detectors" && focusSuspect) selectDetector(focusSuspect, focusWord);
  };

  const syncViewModeUi = () => {
    const isPat = viewMode === "patterns";
    const isDet = viewMode === "detectors";
    const isVocab = viewMode === "vocab";
    const catalog = isPat || isDet || isVocab;
    viewIpsBtn.classList.toggle("active", viewMode === "ips");
    viewPatBtn.classList.toggle("active", isPat);
    viewDetBtn.classList.toggle("active", isDet);
    viewVocabBtn.classList.toggle("active", isVocab);
    title.textContent = isVocab
      ? "词表"
      : isDet
        ? "检测器"
        : isPat
          ? "抽象结果"
          : "改进点";
    prepStepper.hidden = catalog;
    if (catalog) conflictBanner.hidden = true;
    filterCard.hidden = catalog;
    patternFilterCard.hidden = !isPat;
    detectorFilterCard.hidden = !isDet;
    vocabFilterCard.hidden = !isVocab;
    sortHint.hidden = catalog;
    bookLine.textContent = isVocab
      ? "L2 配置词、L2b 词插件、派生特征（不依赖左侧选中的书）"
      : isDet
        ? "跨书症状检测器与它们用到的配置词 / 词插件（不依赖左侧选中的书）"
        : isPat
          ? "全库有成员的可复用模式（不依赖左侧选中的书）"
          : fingerprintHash || jobId
            ? `当前：${jobLabel || "本书"}${fingerprintHash ? ` · ${fingerprintHash.slice(0, 10)}…` : ""}`
            : "左侧选择导入任务，查看这本书的改进台账";
    abstractBtn.hidden = catalog;
    publishViewMode();
    if (isVocab) {
      selectPattern(null);
      syncVocabChips();
      void loadChecks({ force: true });
    } else if (isDet) {
      selectPattern(null);
      selectVocab("");
      void loadChecks({ force: true });
    } else if (isPat) {
      selectDetector("");
      selectVocab("");
      syncPatternChips();
      void loadPatterns({ force: true });
    } else {
      selectPattern(null);
      selectDetector("");
      selectVocab("");
      paintFromCache();
    }
  };

  viewIpsBtn.onclick = () => {
    if (viewMode === "ips") return;
    viewMode = "ips";
    syncViewModeUi();
  };
  viewPatBtn.onclick = () => {
    if (viewMode === "patterns") return;
    viewMode = "patterns";
    syncViewModeUi();
  };
  viewDetBtn.onclick = () => {
    if (viewMode === "detectors") return;
    viewMode = "detectors";
    syncViewModeUi();
  };
  viewVocabBtn.onclick = () => {
    if (viewMode === "vocab") return;
    viewMode = "vocab";
    syncViewModeUi();
  };
  vocabKwInput.oninput = () => {
    vocabKeyword = vocabKwInput.value.trim();
    paintVocabCatalog();
  };
  vocabReloadBtn.onclick = () => {
    void loadChecks({ force: true });
    document.dispatchEvent(new CustomEvent("studio:checks-reload"));
  };
  detKwInput.oninput = () => {
    detectorKeyword = detKwInput.value.trim();
    paintDetectorsCatalog();
  };
  detReloadBtn.onclick = () => {
    void loadChecks({ force: true });
    document.dispatchEvent(new CustomEvent("studio:checks-reload"));
  };
  patKwInput.oninput = () => {
    patternKeyword = patKwInput.value.trim();
    paintPatternsCatalog();
  };
  patReloadBtn.onclick = () => void loadPatterns({ force: true });
  syncPatternChips();

  const paintFromCache = () => {
    if (viewMode === "patterns") {
      paintPatternsCatalog();
      return;
    }
    if (viewMode === "detectors") {
      paintDetectorsCatalog();
      return;
    }
    if (viewMode === "vocab") {
      paintVocabCatalog();
      return;
    }
    listHost.innerHTML = "";
    sortHint.hidden = false;
    const data = lastData;
    if (!data) {
      listHost.appendChild(el("p", "muted", "加载中…"));
      return;
    }
    updateStatusLabels();
    syncPrepChips();
    paintPrepStepper();
    paintConflictBanner();
    detectConflictBtn.disabled = !fingerprintHash;
    syncSplitAllBtn();
    const counts = data.counts || {
      total: 0,
      accepted: 0,
      conflict: 0,
      locus: 0,
      superseded: 0,
      rejected: 0,
      pending: 0,
    };
    hintEl.textContent = hintForList(counts);
    const filteredN = (data.items || []).filter(passFilters).length;
    const sortLab =
      SORT_HEADERS.find((h) => h.key === sortKey)?.label || "页";
    statsEl.textContent =
      `当前筛选 ${filteredN} / ${counts.total} 条 · 已选 ${selected.size} 条` +
      ` · 排序 ${sortLab}${sortDir === "asc" ? "↑" : "↓"}` +
      (fingerprintHash ? ` · 书标识 ${fingerprintHash.slice(0, 10)}…` : "");
    publishContext();

    if (arrange === "by_aggregate") {
      const items = (data.items || [])
        .filter(passFilters)
        .filter((it) => prepOf(it) === "needs_aggregate");
      if (!items.length) {
        listHost.appendChild(
          el(
            "p",
            "muted improvements-empty",
            "当前没有「需聚合」条目。可先「整理」标出对立簇，或改回其它排列。",
          ),
        );
        return;
      }
      const byGid = new Map<string, ImprovementPoint[]>();
      const orphans: ImprovementPoint[] = [];
      for (const it of items) {
        const gid = aggregateGroupIdOf(it);
        if (!gid) {
          orphans.push(it);
          continue;
        }
        const arr = byGid.get(gid) || [];
        arr.push(it);
        byGid.set(gid, arr);
      }
      if (orphans.length) {
        const idList = orphans.map((x) => String(x.id || "")).filter(Boolean);
        const uf = unionFindGroups(idList, conflictPairCache);
        let i = 0;
        for (const [, memberIds] of uf) {
          i += 1;
          const synGid = `pair-cluster-${i}`;
          const members = memberIds
            .map((id) => orphans.find((o) => String(o.id) === id)!)
            .filter(Boolean);
          if (members.length) byGid.set(synGid, members);
        }
      }
      const ordered = [...byGid.entries()].sort((a, b) => {
        const pa = Math.min(
          ...a[1].map((x) => Number(x.page_no) || 9999),
        );
        const pb = Math.min(
          ...b[1].map((x) => Number(x.page_no) || 9999),
        );
        return pa - pb || a[0].localeCompare(b[0]);
      });
      listHost.appendChild(
        el(
          "p",
          "muted improvements-agg-hint",
          "点击簇标题聚焦后显示「处理聚合」；右栏/工具栏「处理全部聚合」→ 全书。",
        ),
      );
      ordered.forEach(([gid, members], i) => {
        const clusterIndex = i + 1;
        const clusterLabel = `簇#${clusterIndex}`;
        const pages = [
          ...new Set(
            members.map((m) => Number(m.page_no) || 0).filter((n) => n > 0),
          ),
        ].sort((a, b) => a - b);
        const reason = aggregateReasonOf(members[0]!) || "对立簇";
        const pageLab = pages.length
          ? `p${pages.join("/")}`
          : "页?";
        // 全文交给 CSS 按行宽省略，勿在 JS 里先截断
        const titleText = `${clusterLabel} · ${pageLab} · ${members.length} 条 · ${reason}`;
        const head = el("div", "improvements-agg-group-head");
        head.dataset.aggGid = gid;
        if (gid === focusClusterKey) head.classList.add("is-focused");
        head.onclick = (ev) => {
          const t = ev.target as HTMLElement | null;
          if (t?.closest("button")) return;
          selectCluster(gid);
        };
        const titleEl = el("span", "improvements-agg-group-title", titleText);
        titleEl.title =
          `${titleText}\n点击聚焦本簇；悬停/聚焦后可点「处理聚合」`;
        const aggBtn = el(
          "button",
          "improvements-row-cta improvements-row-cta--conflict improvements-agg-group-btn",
          "处理聚合",
        ) as HTMLButtonElement;
        aggBtn.type = "button";
        aggBtn.title = `AI 只处理${clusterLabel}（不处理其它簇）`;
        aggBtn.onclick = (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          selectCluster(gid);
          openAggregateGroup(members, gid, clusterIndex);
        };
        head.append(titleEl, aggBtn);
        listHost.appendChild(head);
        renderTable(sortItems(members, sortKey, sortDir));
      });
      return;
    }

    if (arrange === "by_pattern") {
      const accepted = (data.accepted || []).filter(passFilters);
      const byPat = new Map<string, ImprovementPoint[]>();
      for (const it of accepted) {
        const key =
          it.pattern_status === "none" || !it.pattern_id
            ? "__none__"
            : String(it.pattern_id);
        const arr = byPat.get(key) || [];
        arr.push(it);
        byPat.set(key, arr);
      }
      if (!byPat.size) {
        listHost.appendChild(el("p", "muted", "暂无有效改进，无法按可复用规则分组。"));
        return;
      }
      for (const [pid, members] of byPat) {
        const sample = claimSummary(members[0] ?? { id: "" }).slice(0, 36);
        const head =
          pid === "__none__"
            ? `仅本书 · ${members.length} 条`
            : `可复用规则「${sample}${sample.length >= 36 ? "…" : ""}」· ${members.length} 条`;
        listHost.appendChild(el("h3", "edition-ops-h3", head));
        renderTable(members);
      }
      return;
    }

    if (arrange === "by_dialogue") {
      const groups = new Map<string, ImprovementPoint[]>();
      for (const it of (data.items || []).filter(passFilters)) {
        const key = String(it.dialogue_key || `solo:${it.id}`);
        const arr = groups.get(key) || [];
        arr.push(it);
        groups.set(key, arr);
      }
      if (!groups.size) {
        listHost.appendChild(el("p", "muted", "当前筛选下没有记录。"));
        return;
      }
      const ordered = [...groups.entries()].sort((a, b) => {
        const pa = Number(a[1][0]?.page_no ?? 0);
        const pb = Number(b[1][0]?.page_no ?? 0);
        return pa - pb || a[0].localeCompare(b[0]);
      });
      for (const [key, members] of ordered) {
        const first = members[0]!;
        const lead = String(first.dialogue_lead || first.user_input || "").trim();
        const solo = key.startsWith("solo:");
        const head = solo
          ? `p${first.page_no ?? "?"} · 无对话线索 · ${members.length} 条`
          : `p${first.page_no ?? "?"} · 一句用户要求 → ${members.length} 条改进`;
        const h = el("h3", "edition-ops-h3", head);
        if (lead) h.title = lead;
        listHost.appendChild(h);
        if (lead && !solo) {
          const quote = el(
            "p",
            "muted improvements-dialogue-lead",
            lead.slice(0, 120) + (lead.length > 120 ? "…" : ""),
          );
          quote.title = lead;
          listHost.appendChild(quote);
        }
        renderTable(members);
      }
      return;
    }

    if (arrange === "by_problem") {
      const loci = data.by_locus || {};
      const keys = Object.keys(loci).sort();
      let shown = 0;
      for (const lk of keys) {
        const cluster = (loci[lk] || []).filter(passFilters);
        if (!cluster.length) continue;
        shown += 1;
        listHost.appendChild(
          el("h3", "edition-ops-h3", `${lk} · ${cluster.length} 条`),
        );
        renderTable(cluster);
      }
      if (!shown) {
        listHost.appendChild(el("p", "muted", "当前筛选下没有可聚合的问题。"));
      }
      return;
    }

    // 列表
    const items = (data.items || []).filter(passFilters);
    renderTable(items);
  };

  const load = async (opts?: { soft?: boolean }) => {
    const soft = !!opts?.soft;
    if (!soft) {
      listHost.innerHTML = "<p class='muted'>加载中…</p>";
      statsEl.textContent = "";
      hintEl.textContent = "";
    }
    syncBookLine();
    try {
      const fp = fingerprintHash || "";
      const q: { fingerprint_hash?: string; job_id?: string; limit: number } = {
        limit: 1000,
      };
      // 侧栏按真实书指纹列书；有指纹只按指纹筛，避免任务 meta 指纹漂移漏条
      if (fp) q.fingerprint_hash = fp;
      else if (jobId) q.job_id = jobId;
      lastData = await improvementPoints.list(q);
      const items = lastData.items || [];
      if (!soft) {
        selected.clear();
      } else {
        for (const id of [...selected]) {
          if (!items.some((x) => String(x.id) === id)) selected.delete(id);
        }
      }
      const keepFocus = focusId
        ? items.find((x) => String(x.id) === focusId) || null
        : null;
      if (!keepFocus) focusId = "";
      refreshColFilterOptions();
      // 书切换或刷新后对立对缓存失效；按需聚合排列时预拉
      if (conflictPairFp !== fingerprintHash) {
        conflictPairCache = [];
        conflictPairFp = "";
      }
      if (arrange === "by_aggregate") {
        await ensureAggregatePairCache();
      }
      paintFromCache();
      if (keepFocus) selectFocus(keepFocus);
      else if (!soft && !focusId) {
        document.dispatchEvent(
          new CustomEvent("studio:improvements-select", { detail: { item: null } }),
        );
      }
    } catch (err) {
      if (!soft) {
        listHost.innerHTML = "";
        listHost.appendChild(el("p", "error", String(err)));
      }
      publishContext();
    }
  };

  const purgeOne = async (it: ImprovementPoint) => {
    const id = String(it.id || "").trim();
    if (!id) return;
    const sum = claimSummary(it).slice(0, 80);
    const page = it.page_no ?? "?";
    const ok = window.confirm(
      `确定彻底删除 p${page} 这条改进点？\n\n${sum}\n\n将同时删除专书页补丁、关联链接、待审队列引用；不可恢复。`,
    );
    if (!ok) return;
    try {
      await improvementPoints.purge(id);
      selected.delete(id);
      if (focusId === id) {
        focusId = "";
        selectFocus(null);
      }
      await load();
    } catch (err) {
      window.alert(`删除失败：${String(err)}`);
    }
  };

  reloadBtn.onclick = () => {
    if (viewMode === "patterns") void loadPatterns({ force: true });
    else if (viewMode === "detectors" || viewMode === "vocab") {
      void loadChecks({ force: true });
      document.dispatchEvent(new CustomEvent("studio:checks-reload"));
    } else void load({ soft: false });
  };

  const repaintFilters = () => paintFromCache();
  pageInput.oninput = () => {
    pageFilter = pageInput.value.trim();
    repaintFilters();
  };
  kwInput.oninput = () => {
    keyword = kwInput.value.trim();
    repaintFilters();
  };
  clearFilters.onclick = () => {
    originSelected.clear();
    catSelected.clear();
    prepFilter = "";
    pageFilter = "";
    keyword = "";
    pageInput.value = "";
    kwInput.value = "";
    refreshColFilterOptions();
    syncPrepChips();
    repaintFilters();
  };
  syncPrepChips();
  paintPrepStepper();

  // 默认软刷新：保留勾选/焦点；硬刷新仅手动「刷新」或换书
  document.addEventListener(
    "studio:improvements-refresh",
    ((e: CustomEvent<{ soft?: boolean }>) => {
      const soft = e.detail?.soft !== false;
      void load({ soft });
    }) as EventListener,
  );
  document.addEventListener(
    "studio:improvements-fingerprint",
    ((e: CustomEvent) => {
      fingerprintHash = String(e.detail?.fingerprintHash || "").trim();
      void load({ soft: false });
    }) as EventListener,
  );
  document.addEventListener(
    "studio:improvements-filter-status",
    ((_e: CustomEvent) => {
      // 状态列已取消；忽略外部状态筛选事件，仅刷新列表
      paintFromCache();
    }) as EventListener,
  );
  document.addEventListener(
    "studio:improvements-focus",
    ((e: CustomEvent) => {
      jobId = String(e.detail?.jobId || "").trim();
      fingerprintHash = String(e.detail?.fingerprintHash || "").trim();
      jobLabel = String(e.detail?.jobLabel || "").trim();
      persistIpFocus({ jobId, fingerprintHash, jobLabel });
      selected.clear();
      focusId = "";
      focusClusterKey = "";
      selectFocus(null);
      arrange = "list";
      originSelected.clear();
      catSelected.clear();
      pageFilter = "";
      keyword = "";
      pageInput.value = "";
      kwInput.value = "";
      syncArrange();
      void load({ soft: false });
    }) as EventListener,
  );

  // 停留在改进点页时定时软刷整理状态（无需硬刷新）
  let pollTimer: number | null = null;
  const syncPoll = (): void => {
    const on =
      store.activeRibbon === "improvements" &&
      document.visibilityState === "visible";
    if (on && pollTimer == null) {
      pollTimer = window.setInterval(() => {
        if (store.activeRibbon !== "improvements") return;
        if (document.visibilityState !== "visible") return;
        void load({ soft: true });
      }, 7000);
    } else if (!on && pollTimer != null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };
  store.subscribe(syncPoll);
  document.addEventListener("visibilitychange", syncPoll);
  syncPoll();

  // 刷新后恢复上次选中的书
  const boot = loadPersistedIpFocus();
  if (boot && (boot.fingerprintHash || boot.jobId || boot.jobLabel)) {
    jobId = boot.jobId;
    fingerprintHash = boot.fingerprintHash;
    jobLabel = boot.jobLabel || jobLabel;
    document.dispatchEvent(
      new CustomEvent("studio:improvements-focus", {
        detail: {
          jobId,
          fingerprintHash,
          jobLabel: jobLabel || (fingerprintHash ? "本书" : "全部"),
        },
      }),
    );
  } else {
    syncBookLine();
    void load({ soft: false });
  }
  return { refresh: () => void load({ soft: false }) };
}
