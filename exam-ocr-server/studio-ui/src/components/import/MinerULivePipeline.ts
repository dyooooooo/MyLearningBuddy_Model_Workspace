/**
 * MinerU 中栏实时流水线：解析 → 找症状 → 按手册改 → 重跑验活 → 收口。
 * 只服务日常教材（MinerU）线，不读 35B。
 */

import { corpus, type JobSymptoms, type SymptomFinding } from "../../api/corpus";
import type { ImportJob } from "../../api/types";
import { el } from "../../util/dom";

export type PipelineTick = {
  job_id?: string;
  pipeline_phase?: string;
  status?: string;
  pages_ready?: number | string;
  autofix_round?: number;
  autofix_remaining?: number;
  autofix_done?: boolean;
  batch_types?: string[];
  batch_n?: number;
  known_n?: number;
  unknown_n?: number;
  park_reason?: string;
  symptom_rows?: SymptomRow[];
};

export type SymptomRow = {
  suspect_type: string;
  known?: boolean;
  count?: number;
  pages?: number[];
  playbook?: string;
  remediation_kind?: string;
  landing_target?: string;
};

export type LivePipelineHandle = {
  el: HTMLElement;
  arm: (info: { label?: string; editionId?: string; pageCount?: number }) => void;
  bindJob: (jobId: string) => void;
  applyJob: (job: ImportJob) => void;
  applyTick: (tick: PipelineTick) => void;
  applySymptoms: (data: JobSymptoms) => void;
  isArmed: () => boolean;
  /** 解析/自动改还在进行时才需要轮询症状。 */
  needsWatch: () => boolean;
  clear: () => void;
};

type StageId =
  | "stage"
  | "minio"
  | "mineru_parse"
  | "adapter"
  | "render_html"
  | "corpus_tree"
  | "symptom_scan"
  | "autofix"
  | "verify"
  | "close";

type StageState = "idle" | "running" | "done" | "error" | "skip";

type StageDef = {
  id: StageId;
  label: string;
  how: string;
};

const STAGES: StageDef[] = [
  { id: "stage", label: "预上传", how: "PDF 先落到对象存储，再开任务" },
  { id: "minio", label: "任务落盘", how: "创建导入任务，核对对象" },
  { id: "mineru_parse", label: "MinerU 解析", how: "云端拆页、认版式、出内容块" },
  { id: "adapter", label: "转中间表示", how: "内容块套教材方案规则，标栏 / 页眉 / 侧栏" },
  { id: "render_html", label: "渲页", how: "中间表示画成可校对的页 HTML" },
  { id: "corpus_tree", label: "挂目录", how: "按目录树挂到教材节点" },
  { id: "symptom_scan", label: "找症状", how: "检测器扫每一页，点名已知 / 未知问题" },
  { id: "autofix", label: "按手册改", how: "同名症状一批，照手册步骤改中间表示" },
  { id: "verify", label: "重跑验活", how: "改完再扫一遍，还在的开下一轮" },
  { id: "close", label: "收口", how: "活清单没有可改的才算完；否则停靠等继续" },
];

const PARSE_ORDER: StageId[] = [
  "minio",
  "mineru_parse",
  "adapter",
  "render_html",
  "corpus_tree",
];

const KIND_ZH: Record<string, string> = {
  rule: "规则自动修",
  playbook: "按手册修",
};

function readyCount(job: ImportJob): number {
  const raw = job.meta?.pages_ready;
  if (Array.isArray(raw)) return raw.length;
  const n = Number(raw);
  return Number.isFinite(n) ? n : 0;
}

function playbookLead(text: string): string {
  return text
    .split(/\r?\n/)
    .map((s) => s.replace(/^[-*\d.\s]+/, "").trim())
    .filter(Boolean)
    .slice(0, 2)
    .join("；");
}

function pagesLabel(pages: number[] | undefined, limit = 8): string {
  const uniq = [...new Set((pages || []).filter((n) => n > 0))].sort((a, b) => a - b);
  if (!uniq.length) return "";
  const head = uniq.slice(0, limit).join("、");
  return uniq.length > limit ? `第 ${head}… 共 ${uniq.length} 页` : `第 ${head} 页`;
}

function rowsFromSymptoms(data: JobSymptoms): SymptomRow[] {
  const buckets: Array<[SymptomFinding[], boolean]> = [
    [data.known || [], true],
    [data.unknown || [], false],
  ];
  const map = new Map<string, SymptomRow>();
  for (const [items, known] of buckets) {
    for (const f of items) {
      const name = String(f.suspect_type || "").trim();
      if (!name) continue;
      const rec =
        map.get(name) ||
        ({
          suspect_type: name,
          known,
          count: 0,
          pages: [],
          playbook: f.playbook || "",
          remediation_kind: f.remediation_kind || "",
          landing_target: f.landing_target || "",
        } satisfies SymptomRow);
      rec.count = (rec.count || 0) + 1;
      const page = Number(f.page_no || 0);
      if (page > 0 && rec.pages && !rec.pages.includes(page)) rec.pages.push(page);
      if (!rec.playbook && f.playbook) rec.playbook = f.playbook;
      map.set(name, rec);
    }
  }
  return [...map.values()];
}

export function mountMinerULivePipeline(opts: {
  onGotoPage?: (pageNo: number) => void;
}): LivePipelineHandle {
  const root = el("div", "mineru-live-pipeline hidden");
  const head = el("div", "mineru-live-head");
  const title = el("h3", "mineru-live-title", "导入流水线");
  const headline = el("p", "mineru-live-headline", "");
  head.append(title, headline);

  const topo = el("div", "pipeline-topology mineru-live-topo");
  const stageEls = new Map<StageId, HTMLElement>();
  const stageDetail = new Map<StageId, HTMLElement>();
  for (const stage of STAGES) {
    const node = el("div", "pipeline-node node-state-idle");
    node.dataset.stage = stage.id;
    const dot = el("div", "pipeline-node-dot");
    dot.appendChild(el("div", "pipeline-node-spinner"));
    const body = el("div", "pipeline-node-body");
    body.append(
      el("div", "pipeline-node-label", stage.label),
      el("div", "pipeline-node-sublabel", stage.how),
    );
    const detail = el("div", "pipeline-node-detail", "");
    body.appendChild(detail);
    node.append(dot, body);
    topo.appendChild(node);
    stageEls.set(stage.id, node);
    stageDetail.set(stage.id, detail);
  }

  const flowHead = el("h4", "mineru-live-flow-title", "症状怎么消");
  const flowLead = el(
    "p",
    "muted mineru-live-flow-lead",
    "每一项：检出 → 对照手册 → 改中间表示 → 重渲 → 再扫。数量下降才算消掉。",
  );
  const flows = el("div", "mineru-live-flows");

  root.append(head, topo, flowHead, flowLead, flows);

  let armed = false;
  let jobId = "";
  let pageCount = 0;
  let bookLabel = "";
  let parsePhase: StageId | "" = "";
  let currentPhase: StageId | "" = "";
  let pagesReady = 0;
  let jobStatus = "";
  let jobError = "";
  let autofixRound = 0;
  let remaining = -1;
  let autofixDone = false;
  let parked = false;
  let parkReason = "";
  let batchTypes: string[] = [];
  let rows: SymptomRow[] = [];
  const countHistory = new Map<string, number[]>();
  let lastRowSig = "";

  function setStage(id: StageId, state: StageState, detail = ""): void {
    const node = stageEls.get(id);
    if (!node) return;
    node.className = `pipeline-node node-state-${state}`;
    const d = stageDetail.get(id);
    if (d) d.textContent = detail;
  }

  function paintStages(): void {
    const parseIdx = parsePhase ? PARSE_ORDER.indexOf(parsePhase) : -1;
    const parseDone =
      parsePhase === "corpus_tree" ||
      parked ||
      autofixDone ||
      currentPhase === "symptom_scan" ||
      currentPhase === "autofix" ||
      currentPhase === "verify" ||
      currentPhase === "close";

    setStage("stage", armed || jobId ? "done" : "idle", bookLabel || "");
    if (!jobId && armed) {
      setStage("minio", "running", "正在创建导入任务…");
      for (const id of PARSE_ORDER.slice(1)) setStage(id, "idle");
    } else {
      for (let i = 0; i < PARSE_ORDER.length; i++) {
        const id = PARSE_ORDER[i];
        if (jobError && parsePhase === id) {
          setStage(id, "error", jobError);
        } else if (parseDone || (parseIdx >= 0 && i < parseIdx)) {
          const extra =
            id === "render_html" && pageCount
              ? `已出稿 ${pagesReady}/${pageCount}`
              : id === "mineru_parse" && pageCount
                ? `${pagesReady}/${pageCount} 页`
                : "";
          setStage(id, "done", extra);
        } else if (parseIdx === i) {
          const extra =
            pageCount > 0 ? `${pagesReady}/${pageCount} 页` : jobStatus || "进行中";
          setStage(id, "running", extra);
        } else {
          setStage(id, "idle");
        }
      }
    }

    const afterParse =
      parseDone ||
      currentPhase === "symptom_scan" ||
      currentPhase === "autofix" ||
      currentPhase === "verify" ||
      currentPhase === "close";

    if (!afterParse) {
      setStage("symptom_scan", "idle");
      setStage("autofix", "idle");
      setStage("verify", "idle");
      setStage("close", "idle");
    } else if (currentPhase === "symptom_scan") {
      setStage(
        "symptom_scan",
        "running",
        rows.length ? `点名 ${rows.reduce((n, r) => n + (r.count || 0), 0)} 处` : "正在扫页…",
      );
      setStage("autofix", "idle");
      setStage("verify", "idle");
      setStage("close", "idle");
    } else if (currentPhase === "autofix") {
      setStage("symptom_scan", "done", remaining >= 0 ? `活清单 ${remaining} 处` : "");
      setStage(
        "autofix",
        "running",
        `第 ${autofixRound || 1} 轮` + (batchTypes.length ? ` · ${batchTypes.join("、")}` : ""),
      );
      setStage("verify", "idle", "改完再扫");
      setStage("close", "idle");
    } else if (currentPhase === "verify") {
      setStage("symptom_scan", "done");
      setStage("autofix", "done", autofixRound ? `已跑 ${autofixRound} 轮` : "");
      setStage("verify", "running", remaining >= 0 ? `还剩 ${remaining} 处` : "重跑检测…");
      setStage("close", "idle");
    } else if (parked) {
      setStage("symptom_scan", "done");
      setStage("autofix", "done");
      setStage("verify", "done");
      setStage("close", "error", parkReason || "未收口，点继续再跑");
    } else if (autofixDone || currentPhase === "close") {
      setStage("symptom_scan", "done");
      setStage("autofix", "done");
      setStage("verify", "done");
      setStage("close", "done", "活清单已无有手册可改的症状");
    } else {
      setStage("symptom_scan", "running", "等待首轮检测…");
      setStage("autofix", "idle");
      setStage("verify", "idle");
      setStage("close", "idle");
    }
  }

  function paintHeadline(): void {
    const bits: string[] = [];
    if (bookLabel) bits.push(bookLabel);
    if (pageCount) bits.push(`${pageCount} 页`);
    if (currentPhase === "autofix" || currentPhase === "verify") {
      bits.push(`自动改第 ${autofixRound || 1} 轮`);
      if (remaining >= 0) bits.push(`还剩 ${remaining} 处可改`);
    } else if (autofixDone) {
      bits.push("自动改已收口");
    } else if (parked) {
      bits.push(parkReason || "自动改未收口");
    } else if (pagesReady || pageCount) {
      bits.push(`已出稿 ${pagesReady}${pageCount ? `/${pageCount}` : ""}`);
    }
    headline.textContent = bits.join(" · ");
  }

  function microState(
    row: SymptomRow,
  ): Array<{ key: string; label: string; state: StageState; note: string }> {
    const name = row.suspect_type;
    const hist = countHistory.get(name) || [row.count || 0];
    const first = hist[0] ?? 0;
    const now = hist[hist.length - 1] ?? 0;
    const dropped = now < first;
    const gone = now === 0;
    const inBatch = batchTypes.includes(name);
    const scanning = currentPhase === "symptom_scan";
    const fixing = currentPhase === "autofix" && inBatch;
    const verifying = currentPhase === "verify" || (currentPhase === "autofix" && !inBatch && autofixRound > 1);

    const kind = KIND_ZH[row.remediation_kind || ""] || (row.known ? "按手册修" : "还没有手册");
    const land = row.landing_target ? `生效面 ${row.landing_target}` : "";
    const lead = playbookLead(row.playbook || "");

    return [
      {
        key: "find",
        label: "检出",
        state: first > 0 ? "done" : scanning ? "running" : "idle",
        note: first > 0 ? `${first} 处 · ${pagesLabel(row.pages)}` : "尚未点名",
      },
      {
        key: "book",
        label: "对照手册",
        state: row.known && lead ? "done" : row.known ? "done" : "idle",
        note: row.known ? [kind, land, lead].filter(Boolean).join(" · ") : "未知疑点，先诊断再沉淀",
      },
      {
        key: "edit",
        label: "改中间表示",
        state: gone ? "done" : fixing ? "running" : dropped ? "done" : "idle",
        note: fixing ? "本批正在改" : gone || dropped ? "已按手册改过" : "等轮到这一类",
      },
      {
        key: "render",
        label: "重渲该页",
        state: gone ? "done" : fixing ? "running" : dropped ? "done" : "idle",
        note: fixing ? "改完立刻重画页 HTML" : dropped || gone ? "页已重画" : "",
      },
      {
        key: "recheck",
        label: "再扫",
        state: gone ? "done" : verifying ? "running" : dropped ? "done" : "idle",
        note: gone
          ? "已消掉"
          : dropped
            ? `${first} → ${now} 处`
            : verifying
              ? "重跑检测中"
              : now > 0
                ? `还在 ${now} 处`
                : "",
      },
    ];
  }

  function paintFlows(): void {
    flows.replaceChildren();
    if (!rows.length) {
      flows.appendChild(
        el(
          "p",
          "muted",
          currentPhase === "symptom_scan" || parsePhase
            ? "还没点名症状。解析出稿后会在这里逐项展开。"
            : "点「AI 全书导入」或「开始解析」后，这里跟整条线。",
        ),
      );
      return;
    }
    for (const row of rows) {
      const card = el("div", "mineru-live-symptom");
      if (row.known === false) card.classList.add("is-unknown");
      if ((row.count || 0) === 0) card.classList.add("is-cleared");
      const hist = countHistory.get(row.suspect_type) || [];
      const now = row.count || 0;
      const first = hist[0] ?? now;
      const head = el("div", "mineru-live-symptom-head");
      const name = el("span", "mineru-live-symptom-name", row.suspect_type);
      const tally = el(
        "span",
        "mineru-live-symptom-tally",
        now === 0 ? "已消掉" : first !== now ? `${first} → ${now} 处` : `${now} 处`,
      );
      head.append(name, tally);
      card.appendChild(head);

      const steps = el("div", "mineru-live-micro");
      for (const step of microState(row)) {
        const cell = el("div", `mineru-live-micro-step is-${step.state}`);
        cell.append(
          el("span", "mineru-live-micro-label", step.label),
          el("span", "muted mineru-live-micro-note", step.note),
        );
        steps.appendChild(cell);
      }
      card.appendChild(steps);

      const pages = (row.pages || []).slice(0, 16);
      if (pages.length && opts.onGotoPage) {
        const nav = el("div", "mineru-live-pages");
        for (const p of pages) {
          const btn = el("button", "btn-link", `第 ${p} 页`) as HTMLButtonElement;
          btn.type = "button";
          btn.onclick = () => opts.onGotoPage?.(p);
          nav.appendChild(btn);
        }
        if ((row.pages || []).length > 16) {
          nav.appendChild(el("span", "muted", `… 另 ${(row.pages || []).length - 16} 页`));
        }
        card.appendChild(nav);
      }
      flows.appendChild(card);
    }
  }

  function rememberCounts(next: SymptomRow[]): void {
    for (const row of next) {
      const n = row.count || 0;
      const hist = countHistory.get(row.suspect_type) || [];
      if (!hist.length || hist[hist.length - 1] !== n) hist.push(n);
      countHistory.set(row.suspect_type, hist);
    }
    for (const [name, hist] of countHistory) {
      if (next.some((r) => r.suspect_type === name)) continue;
      if (hist[hist.length - 1] !== 0) hist.push(0);
    }
  }

  function show(): void {
    root.classList.remove("hidden");
  }

  function paint(): void {
    if (!armed && !jobId) {
      root.classList.add("hidden");
      return;
    }
    show();
    paintStages();
    paintHeadline();
    paintFlows();
  }

  function mapJobPhase(phase: string): StageId | "" {
    if (PARSE_ORDER.includes(phase as StageId)) return phase as StageId;
    if (phase === "done") return "corpus_tree";
    if (phase === "refine_35b") return "adapter";
    if (
      phase === "symptom_scan" ||
      phase === "autofix" ||
      phase === "verify" ||
      phase === "close"
    ) {
      return phase;
    }
    if (phase === "parked") return "close";
    return "";
  }

  return {
    el: root,
    arm(info) {
      armed = true;
      bookLabel = String(info.label || bookLabel || "");
      pageCount = Number(info.pageCount || pageCount || 0);
      if (!jobId) currentPhase = "minio";
      paint();
    },
    bindJob(id) {
      jobId = String(id || "").trim();
      armed = true;
      if (jobId && currentPhase === "minio") currentPhase = "";
      paint();
    },
    applyJob(job) {
      if (!job) return;
      jobId = job.id || jobId;
      armed = true;
      jobStatus = String(job.status || "");
      jobError = String(job.error || "");
      bookLabel = String(job.label || bookLabel || "");
      const meta = job.meta || {};
      pageCount = Number(
        meta.total_pages || meta.ocr_total || meta.page_count || job.page_count || pageCount || 0,
      );
      pagesReady = readyCount(job);
      const phase = mapJobPhase(String(meta.pipeline_phase || ""));
      if (phase && PARSE_ORDER.includes(phase)) {
        parsePhase = phase;
        if (!currentPhase || currentPhase === "minio") currentPhase = "";
      } else if (phase) {
        currentPhase = phase;
        if (!parsePhase) parsePhase = "corpus_tree";
      }
      if (jobError) currentPhase = currentPhase || parsePhase || "mineru_parse";
      paint();
    },
    applyTick(tick) {
      armed = true;
      if (tick.job_id) jobId = String(tick.job_id);
      if (tick.pages_ready != null) {
        const n = Number(tick.pages_ready);
        if (Number.isFinite(n)) pagesReady = n;
      }
      if (tick.status) jobStatus = String(tick.status);
      const phase = mapJobPhase(String(tick.pipeline_phase || ""));
      if (phase && PARSE_ORDER.includes(phase)) {
        parsePhase = phase;
      } else if (phase) {
        currentPhase = phase;
        if (!parsePhase) parsePhase = "corpus_tree";
      }
      if (tick.autofix_round != null) autofixRound = Number(tick.autofix_round) || 0;
      if (tick.autofix_remaining != null) remaining = Number(tick.autofix_remaining);
      if (tick.autofix_done) {
        autofixDone = true;
        parked = false;
        currentPhase = "close";
      }
      if (tick.pipeline_phase === "parked") {
        parked = true;
        parkReason = String(tick.park_reason || "");
        currentPhase = "close";
      }
      if (tick.batch_types) batchTypes = tick.batch_types.map(String).filter(Boolean);
      if (tick.symptom_rows?.length) {
        rows = tick.symptom_rows;
        rememberCounts(rows);
      }
      paint();
    },
    applySymptoms(data) {
      const next = rowsFromSymptoms(data);
      const sig = next.map((r) => `${r.suspect_type}:${r.count || 0}`).join("|");
      if (sig === lastRowSig && rows.length) return;
      lastRowSig = sig;
      rows = next;
      rememberCounts(rows);
      const total = (data.known || []).length + (data.unknown || []).length;
      if (remaining < 0) remaining = (data.known || []).length;
      if (total > 0 && !currentPhase && parsePhase) currentPhase = "symptom_scan";
      paint();
    },
    isArmed: () => armed || !!jobId,
    needsWatch: () => {
      if (!(armed || jobId) || autofixDone || parked || currentPhase === "close") {
        return false;
      }
      if (
        currentPhase === "symptom_scan" ||
        currentPhase === "autofix" ||
        currentPhase === "verify"
      ) {
        return true;
      }
      return !!parsePhase && parsePhase !== "corpus_tree";
    },
    clear() {
      armed = false;
      jobId = "";
      pageCount = 0;
      bookLabel = "";
      parsePhase = "";
      currentPhase = "";
      pagesReady = 0;
      jobStatus = "";
      jobError = "";
      autofixRound = 0;
      remaining = -1;
      autofixDone = false;
      parked = false;
      parkReason = "";
      batchTypes = [];
      rows = [];
      countHistory.clear();
      lastRowSig = "";
      root.classList.add("hidden");
      flows.replaceChildren();
      headline.textContent = "";
      for (const id of STAGES.map((s) => s.id)) setStage(id, "idle");
    },
  };
}

/** 绑定任务后拉症状清单。只在流水线还活着时续问，清单没变不重画。 */
export function startSymptomWatch(
  handle: LivePipelineHandle,
  jobId: string,
): () => void {
  let stopped = false;
  const tick = async () => {
    if (stopped || !jobId) return;
    const data = await corpus.jobSymptoms(jobId).catch(() => null);
    if (!stopped && data) handle.applySymptoms(data);
    if (!handle.needsWatch()) {
      stopped = true;
      window.clearInterval(timer);
    }
  };
  void tick();
  const timer = window.setInterval(() => void tick(), 8000);
  return () => {
    stopped = true;
    window.clearInterval(timer);
  };
}
