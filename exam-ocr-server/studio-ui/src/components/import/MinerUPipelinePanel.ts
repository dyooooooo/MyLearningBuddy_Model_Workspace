// MinerU line import panel (corpus). Fully isolated from the 35B panel.
//
// Viewport-driven preview: light shells for 1..N, load ±radius, unload far pages.
// Page/block edits go through AI dialog (double-click focus).

import { corpus, importPdf, probePdf } from "../../api/corpus";
import type { ImportJob } from "../../api/types";
import {
  startImportProgressPolling,
} from "../../services/importProgress";
import { IMPORT_JOB_TERMINAL } from "../../services/importJobObserver";
import { store } from "../../state/store";
import { studioBus } from "../../events/studioBus";
import { sanitizeHtml, TEXTBOOK_CSS_HREF, TEXTBOOK_CSS_VERSION } from "../../util/html-sanitize";
import { typesetTextbookMathIdle } from "../../util/math-render";
import {
  createPageViewportController,
  type PageViewportHandle,
} from "../../util/PageViewportController";
import { el } from "../../util/dom";
import { mountCorpusPageDebugDrawer } from "./CorpusPageDebugDrawer";
import {
  emitMinerUPreviewState,
  MINERU_PAGE_SOFT_REFRESH,
  scheduleMinerUTreeRefresh,
} from "./MinerUImportTree";
import { mountKnownBookChoice } from "./knownBookChoice";
import { mountStickyPageStrip } from "./StickyPageStrip";
import { mountSymptomsBar } from "./SymptomsBar";
import {
  mountMinerULivePipeline,
  startSymptomWatch,
} from "./MinerULivePipeline";
import { mountImportCompare } from "./ImportCompareView";
import { setWorkflowFocus } from "../../state/workflowDebug";
import type { PanelHandle } from "./types";

function emitFocusTarget(jobId: string, pageNo: number, blockId?: string): void {
  const detail = {
    line: "mineru" as const,
    jobId,
    pageNo,
    ...(blockId ? { blockId } : {}),
  };
  studioBus.emit("studio:focus-import-target", detail);
  studioBus.emit("studio:open-ai-terminal");
}

const TERMINAL = IMPORT_JOB_TERMINAL;

/** Indent rules that must win even if textbook.css link is stale/cached. */
const CRITICAL_TEXTBOOK_CSS = `
:root {
  --lb-indent: 2em;
  --lb-marker-optical: 0.6em;
  --lb-page-max: 58rem;
  --lb-page-pad-y: 3.25rem;
  --lb-page-pad-x: 3.5rem;
  --lb-col-gap: 1.65rem;
  --lb-aside-gutter: 0.85rem;
  --lb-canvas: #eef1f6;
}
.lb-page {
  max-width: var(--lb-page-max) !important;
  margin-left: auto !important;
  margin-right: auto !important;
  padding: var(--lb-page-pad-y) var(--lb-page-pad-x) !important;
}
.lb-grid {
  gap: var(--lb-col-gap) !important;
}
.lb-aside--left {
  padding-right: var(--lb-aside-gutter) !important;
}
.lb-aside--right {
  padding-left: var(--lb-aside-gutter) !important;
}
/* 正文必须左对齐：旧 textbook.css 缓存若仍是 justify 会「抻面条」 */
.lb-main .lb-block > p {
  text-align: left !important;
  text-justify: auto !important;
  word-spacing: normal !important;
}
.lb-main .lb-block > p.lb-list-item,
.lb-main .lb-block > p.lb-solution-lead {
  text-indent: 0 !important;
  padding-left: var(--lb-indent) !important;
  text-align: left !important;
}
.lb-main .lb-block > p.lb-solution-lead {
  display: flex !important;
  align-items: flex-start;
  gap: 0;
}
.lb-solution-label {
  flex: 0 0 var(--lb-indent) !important;
  box-sizing: border-box;
  padding-left: var(--lb-marker-optical) !important;
  line-height: inherit;
}
.lb-solution-tail, .lb-solution-body { flex: 1; min-width: 0; }
.lb-main .lb-block > p.lb-solution-item {
  text-indent: 0 !important;
  padding-left: calc(var(--lb-indent) * 2) !important;
  text-align: left !important;
}
.lb-main .lb-block > .lb-solution {
  display: flex !important;
  align-items: flex-start;
  padding-left: var(--lb-indent) !important;
  margin: 0 0 0.65rem;
  gap: 0;
}
.lb-main .lb-block > .lb-table-wrap {
  margin: 0.75rem calc(var(--lb-indent) * 2) !important;
  width: auto !important;
  box-sizing: border-box;
}
`.trim();

function ensureCss(): void {
  const href = `${TEXTBOOK_CSS_HREF}?v=${TEXTBOOK_CSS_VERSION}`;
  const existing = document.querySelectorAll<HTMLLinkElement>('link[data-lb-textbook-css="1"]');
  // Keep the current stylesheet if href already matches — removing it blanks the preview.
  if (existing.length === 1 && existing[0].href.includes(TEXTBOOK_CSS_VERSION)) {
    return;
  }
  existing.forEach((n) => n.remove());
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.dataset.lbTextbookCss = "1";
  document.head.appendChild(link);
}

function ensureCriticalCss(root: HTMLElement): void {
  let style = root.querySelector<HTMLStyleElement>("style[data-lb-critical-css]");
  if (!style) {
    style = document.createElement("style");
    style.dataset.lbCriticalCss = "1";
    root.prepend(style);
  }
  style.dataset.lbCriticalVer = TEXTBOOK_CSS_VERSION;
  style.textContent = CRITICAL_TEXTBOOK_CSS;
}

export function mountMinerUPipelinePanel(): PanelHandle {
  ensureCss();

  const setupRoot = el("div", "import-line-setup import-line-mineru-setup");
  const previewRoot = el("div", "import-line-preview import-line-mineru-preview");
  ensureCriticalCss(previewRoot);

  const form = el("div", "import-upload-card");
  const formHead = el("div", "import-upload-head");
  formHead.append(
    el("h3", "", "MinerU 精准解析 · 新建导入"),
    el("p", "muted import-upload-sub", "上传教材 PDF，选定解析方案后开始出稿"),
  );

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "application/pdf";

  const labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.placeholder = "可选；留空则用 PDF 文件名";
  labelInput.className = "import-input";
  labelInput.title = "可选。不填时自动使用所选 PDF 的文件名（去掉 .pdf）";

  /** 教材标签：用户输入优先；为空则用 PDF 文件名（去扩展名）。 */
  const resolveLabel = (file: File): string => {
    const typed = labelInput.value.trim();
    if (typed) return typed;
    return file.name.replace(/\.pdf$/i, "").trim() || file.name;
  };

  // 教材方案下拉：现有方案 + 「新建方案」「AI 方案」两个动作项
  const EDITION_ACTION_NEW = "__new_edition__";
  const editionSelect = document.createElement("select");
  editionSelect.className = "import-input";
  editionSelect.title = "教材解析方案（必选）：决定分栏识别与校对规则；AI 全书导入也须先选方案";
  editionSelect.setAttribute("aria-label", "教材解析方案");
  const editionPlaceholder = document.createElement("option");
  editionPlaceholder.value = "";
  editionPlaceholder.textContent = "选择教材方案…";
  // 占位项：不可选、不出现在下拉列表里，仅在无方案可选时作为空状态显示
  editionPlaceholder.disabled = true;
  editionPlaceholder.hidden = true;
  const editionGroup = document.createElement("optgroup");
  editionGroup.label = "现有方案";
  const actionGroup = document.createElement("optgroup");
  actionGroup.label = "更多操作";
  const optNew = document.createElement("option");
  optNew.value = EDITION_ACTION_NEW;
  optNew.textContent = "＋ 新建方案（打开方案页）";
  actionGroup.append(optNew);
  editionSelect.append(editionPlaceholder, editionGroup, actionGroup);

  /** 最近一次有效选择（选中动作项后回滚到它）。 */
  let lastEditionValue = "";
  void import("../../api/editionOps")
    .then(({ editionOps }) => editionOps.list())
    .then(({ editions }) => {
      for (const e of editions) {
        if (e.id === "_base") continue;
        const opt = document.createElement("option");
        opt.value = e.id;
        opt.textContent = `${e.display_name || e.id}`;
        editionGroup.appendChild(opt);
      }
      if ([...editionSelect.options].some((o) => o.value === "hujiao_math_hs_v1")) {
        editionSelect.value = "hujiao_math_hs_v1";
        lastEditionValue = editionSelect.value;
      }
    })
    .catch(() => {
      const opt = document.createElement("option");
      opt.value = "hujiao_math_hs_v1";
      opt.textContent = "hujiao_math_hs_v1";
      editionGroup.appendChild(opt);
      editionSelect.value = "hujiao_math_hs_v1";
      lastEditionValue = editionSelect.value;
    });

  const pageLimitInput = document.createElement("input");
  pageLimitInput.type = "number";
  pageLimitInput.min = "0";
  pageLimitInput.placeholder = "0 = 全部页";
  pageLimitInput.className = "import-input";

  const probeHint = el("span", "import-probe-hint", "");
  // 同一本书重导：probe 顺带回历史候选，命中就让用户选「重新生成 / 继承上次」
  const knownBook = mountKnownBookChoice({
    onOpenPrevious: (jobId) => {
      store.showImportPreview();
      openMinerUPreview(jobId);
      void restoreAiForJob(jobId);
      msg.textContent = `已打开上次的导入任务 ${jobId.slice(0, 8)}`;
    },
  });
  fileInput.onchange = async () => {
    probeHint.textContent = "";
    knownBook.clear();
    const f = fileInput.files?.[0];
    if (!f) return;
    // Copilot 会话进行中换/补选 PDF：自动预上传并把 minio_key 注入会话（Agent 自己接管导入）
    void notifyCopilotNewPdf(f);
    try {
      const info = await probePdf(f);
      probeHint.textContent = `已探测 · 共 ${info.page_count ?? info.pages ?? "?"} 页`;
      knownBook.update(info?.known_books);
    } catch {
      probeHint.textContent = "";
    }
  };

  /** 活动 Copilot 会话存在时，把新选的 PDF 预上传到 MinIO staging 并作为消息送达 Agent。 */
  const notifyCopilotNewPdf = async (f: File): Promise<void> => {
    const { hasActiveCopilot, copilotSendText } = await import("../CopilotPanel");
    const { getImportCopilotHost } = await import("../Inspector");
    const host = getImportCopilotHost() || copilotHost;
    if (!hasActiveCopilot(host)) return;
    try {
      const { stagePdf } = await import("../../api/corpus");
      const staged = await stagePdf(f);
      copilotSendText(
        host,
        `我刚在导入面板选择了 PDF，已预上传完成：minio_key=${staged.minio_key}` +
          `（文件名 ${staged.filename}` +
          (staged.page_count ? `，共 ${staged.page_count} 页` : "") +
          `）。请基于这份文件继续执行。`,
      );
    } catch (e) {
      console.warn("PDF 预上传失败", e);
      copilotSendText(host, `我选择了新的 PDF「${f.name}」，但预上传失败，请稍后重试。`);
    }
  };

  const startBtn = el("button", "btn-primary", "开始解析 (MinerU)") as HTMLButtonElement;
  startBtn.type = "button";
  const msg = el("span", "import-msg", "");

  // Copilot 内嵌面板挂载点：出现在导入表单正下方（中栏内，不弹层）
  const copilotHost = el("div", "copilot-host");
  setupRoot.append(form, copilotHost);

  const previewWrap = el("div", "import-preview-wrap");
  const previewPlaceholder = el(
    "p",
    "import-preview-placeholder muted",
    "从左侧选择 MinerU 导入任务",
  );
  const pendingBar = el("div", "muted import-mineru-pending hidden", "");
  const livePipeline = mountMinerULivePipeline({
    onGotoPage: (pageNo) => {
      void scrollToPageWhenReady(pageNo);
    },
  });
  const symptoms = mountSymptomsBar({
    onGotoPage: (pageNo) => {
      void scrollToPageWhenReady(pageNo);
    },
    onFix: (finding) => {
      const host = copilotHost;
      const lines = [
        "【按手册修一条已知症状】",
        `job_id=${previewJobId}`,
        `page_no=${finding.page_no || 0}`,
        `block_id=${finding.block_id || ""}`,
        `suspect_type=${finding.suspect_type}`,
        `处置档位=${finding.remediation_kind || "未定"}`
          + (finding.landing_target ? ` · 生效面=${finding.landing_target}` : ""),
      ];
      if (finding.playbook) {
        lines.push("手册步骤：", finding.playbook);
        if (finding.playbook_ref) lines.push(`（出处 ${finding.playbook_ref}）`);
      } else if (finding.playbook_ref) {
        lines.push(`手册出处：${finding.playbook_ref}`);
      }
      for (const c of finding.candidates || []) lines.push(c);
      lines.push("请照这条成功路径改，改完以最终页 HTML 验收。");
      void import("../CopilotPanel").then(({ copilotPrefillText }) => {
        if (!copilotPrefillText(host, lines.join("\n"), finding.suspect_type)) {
          window.alert("AI 对话未就绪：请先在本页打开 AI 对话，再点「按手册修」。");
        }
      });
    },
  });
  const previewPages = el("div", "import-preview-pages");
  // 页码条稍后挂上（等 scrollToPageWhenReady 定义），避免打包器 TDZ 问题
  const pageStripHost = el("div", "studio-sticky-page-strip-host");
  previewWrap.append(
    previewPlaceholder,
    livePipeline.el,
    pendingBar,
    symptoms.el,
    pageStripHost,
    previewPages,
  );
  const compare = mountImportCompare({
    previewWrap,
    previewPages,
    insertToolbarBefore: previewPages,
    htmlLabel: "识别结果",
    imageLabel: "原始页图",
    loadImageSrc: async (jobId, pageNo) => {
      const out = await corpus.pageDebugArtifact(jobId, pageNo, "page_image");
      const b64 = out?.bytes_b64 ? String(out.bytes_b64) : "";
      return b64 ? `data:image/jpeg;base64,${b64}` : null;
    },
    getActivePage: () => activePageNo,
    onRefreshPage: (pageNo) => softRefreshMountedPage(pageNo),
    onModeChange: (_on, keepPage) => {
      // Restore BEFORE heal/refresh — wrap/unwrap already moved scrollTop to a wrong page.
      const page = keepPage > 0 ? keepPage : activePageNo;
      if (page > 0) {
        activePageNo = page;
        emitPageNavState();
        ensurePageShell(page);
        viewport?.scrollToPage(page);
      }
      // Defer heal so neighbor loads don't fight the pin realign.
      requestAnimationFrame(() => {
        viewport?.refresh();
        viewport?.healVisible();
        if (page > 0) {
          viewport?.scrollToPage(page);
          enqueueWarmPages([page - 1, page, page + 1, page + 2, page + 3].filter((p) => p > 0));
        }
      });
      // Image cells finish loading later and grow — one more pin after layout settles.
      window.setTimeout(() => {
        if (page > 0 && activePageNo === page) viewport?.scrollToPage(page);
      }, 280);
    },
  });
  previewRoot.appendChild(previewWrap);

  const grid = el("div", "import-form-grid");

  const fileField = el("label", "import-field import-field--wide");
  fileField.append(el("span", "import-field-label", "PDF 文件"), fileInput, probeHint);

  const labelField = el("label", "import-field");
  labelField.append(el("span", "import-field-label", "教材标签（可选）"), labelInput);

  const pageField = el("label", "import-field");
  pageField.append(el("span", "import-field-label", "页数上限"), pageLimitInput);

  const editionWrap = document.createElement("label");
  editionWrap.className = "import-field import-field--wide";
  const editionHead = el("span", "import-field-head");
  editionHead.append(document.createTextNode("教材方案"));
  editionWrap.append(editionHead, editionSelect);

  editionSelect.onchange = () => {
    const v = editionSelect.value;
    if (v === EDITION_ACTION_NEW) {
      editionSelect.value = lastEditionValue;
      store.setActiveRibbon("edition-ops");
      return;
    }
    lastEditionValue = v;
  };

  const aiBtn = document.createElement("button");
  aiBtn.type = "button";
  aiBtn.className = "btn btn-secondary";
  aiBtn.textContent = "AI 全书导入";
  aiBtn.title =
    "须先选教材方案与 PDF：MinerU 全书一次识别 → 中栏跟流水线（找症状 / 按手册改）→ 右栏 Cursor 自动改";
  aiBtn.onclick = () => {
    void (async () => {
      const eid = editionSelect.value.trim();
      if (!eid || eid.startsWith("__")) {
        msg.textContent = "请先选择教材方案，再点 AI 全书导入";
        return;
      }
      const f = fileInput.files?.[0];
      if (!f) {
        msg.textContent = "请先选择 PDF 文件";
        return;
      }
      // 同一本书重导且用户选「打开上次的任务」：不重跑、不花云解析额度
      if (knownBook.mode() === "open_previous") {
        const prev = knownBook.sourceJobId();
        if (prev) {
          store.showImportPreview();
          openMinerUPreview(prev);
          void restoreAiForJob(prev);
          msg.textContent = `已打开上次的导入任务 ${prev.slice(0, 8)}`;
          return;
        }
      }
      aiBtn.disabled = true;
      msg.textContent = "正在预上传并打开右栏 AI…";
      try {
        const { stagePdf } = await import("../../api/corpus");
        const staged = await stagePdf(f);
        // 新选 PDF = 新导入线：禁止沿用中栏/本地缓存的旧 job（否则会绑错对话）
        clearPreview();
        const { clearActiveMinerUJob } = await import("./activeMinerUJob");
        clearActiveMinerUJob();
        const payload: Record<string, unknown> = {
          label: resolveLabel(f),
          edition_id: eid,
          source_edition_id: eid,
          minio_key: staged.minio_key,
          filename: staged.filename,
        };
        if (staged.page_count) payload.page_count = staged.page_count;
        // 继承上次结果：来源任务写进会话 payload，corpus.start_import 会自己带上
        const inheritFrom = knownBook.mode() === "inherit" ? knownBook.sourceJobId() : "";
        if (inheritFrom) payload.inherit_from_job_id = inheritFrom;
        // 刻意不带 job_id / import_job_id：等 AI 真正 create 任务后再绑定
        const { getImportCopilotHost, showImportCopilotTab } = await import("../Inspector");
        const { openCopilotPanel, closeCopilotPanel } = await import("../CopilotPanel");
        const host = getImportCopilotHost() || copilotHost;
        showImportCopilotTab();
        closeCopilotPanel(host);
        openCopilotPanel({ kind: "import_advice", host, payload, title: "AI 全书导入" });
        store.showImportPreview();
        showJobOverview();
        livePipeline.arm({
          label: String(payload.label || ""),
          editionId: eid,
          pageCount: Number(staged.page_count || 0),
        });
        syncPlaceholder();
        msg.textContent =
          "中栏已打开流水线；右栏新对话创建全书任务后会自动跟进度" +
          (inheritFrom ? `（${knownBook.summary()}）` : "");
      } catch (e) {
        msg.textContent = `AI 启动失败：${e instanceof Error ? e.message : String(e)}`;
      } finally {
        aiBtn.disabled = false;
      }
    })();
  };

  const actions = el("div", "import-field import-field--actions");
  actions.append(startBtn, aiBtn, msg);

  grid.append(fileField, labelField, pageField, editionWrap, knownBook.el, actions);
  form.append(formHead, grid);

  async function restoreAiForJob(jobId: string): Promise<void> {
    const jid = String(jobId || "").trim();
    if (!jid) return;
    try {
      const { getImportCopilotHost, showImportCopilotTab } = await import("../Inspector");
      const { tryRestoreCopilotPanel } = await import("../CopilotPanel");
      const host = getImportCopilotHost() || copilotHost;
      const bound = host.getAttribute("data-copilot-job") || "";
      // 1:1 绑定：有历史则恢复；无历史则自动建空对话（可聚焦改页）
      const ok = await tryRestoreCopilotPanel({
        kind: "import_advice",
        host,
        title: "AI · 本任务对话",
        jobId: jid,
        force: !!bound && bound !== jid,
        createIfMissing: true,
      });
      showImportCopilotTab();
      if (!ok) {
        console.warn("无法为导入任务绑定/创建 AI 对话", jid);
      }
    } catch (e) {
      console.warn("恢复 Copilot 会话失败", e);
    }
  }

  let previewJobId = "";
  /** 症状清单已为哪个任务加载过：终态会反复回调，别每次都打接口 */
  let symptomsLoadedFor = "";
  let previewTextbookId = "";
  let previewTotal = 0;
  /** Shells present in DOM (1..N). */
  let mountedPages = new Set<number>();
  /** Pages that currently hold HTML in the viewport window. */
  let loadedPages = new Set<number>();
  let pageBodies = new Map<number, HTMLElement>();
  let pageWraps = new Map<number, HTMLElement>();
  /** Cheap string cache — survives unload so scroll-back is fast. */
  let htmlCache = new Map<number, string>();
  let readyPageSet = new Set<number>();
  let debugMounted = new Set<number>();
  let previewJobUnsub: (() => void) | null = null;
  let symptomWatchStop: (() => void) | null = null;
  let pendingScrollPage = 0;
  let activePageNo = 0;
  /** job = 点书名看症状总览；page = 点页码只看该页。两块互斥，不叠在同一栏。 */
  let centerMode: "job" | "page" = "job";
  let viewport: PageViewportHandle | null = null;
  /** Prefetch ±4; keep a wider hot window so adjacent flips rarely unload. */
  const VIEWPORT_RADIUS = 4;
  const VIEWPORT_KEEP = 8;
  /** Background HTML warm-cache (ready pages → htmlCache before scroll). */
  const WARM_CONCURRENCY = 6;
  const warmInflight = new Set<number>();
  let warmQueue: number[] = [];
  /** Bumped on hard refresh so in-flight warm cannot overwrite fresher HTML. */
  const pageFetchEpoch = new Map<number, number>();
  let pageStrip: ReturnType<typeof mountStickyPageStrip> | null = null;
  /** Periodic heal: stop-scroll blanks are invisible to IntersectionObserver. */
  let healTimer: ReturnType<typeof setInterval> | null = null;

  function bumpPageEpoch(pageNo: number): number {
    const next = (pageFetchEpoch.get(pageNo) || 0) + 1;
    pageFetchEpoch.set(pageNo, next);
    return next;
  }

  function pageEpoch(pageNo: number): number {
    return pageFetchEpoch.get(pageNo) || 0;
  }

  function syncPageStrip(): void {
    if (!pageStrip) return;
    if (!previewJobId || previewTotal <= 0) {
      pageStrip.setPages([]);
      return;
    }
    const pages = Array.from({ length: previewTotal }, (_, i) => i + 1);
    pageStrip.setPages(pages);
    if (activePageNo > 0) pageStrip.setActive(activePageNo);
  }

  function emitPageNavState(): void {
    if (!previewJobId) {
      emitMinerUPreviewState({ active: false });
      syncPageStrip();
      return;
    }
    emitMinerUPreviewState({
      active: true,
      jobId: previewJobId,
      textbookNodeId: previewTextbookId || undefined,
      activePageNo: activePageNo || undefined,
    });
    setWorkflowFocus({
      line: "mineru",
      jobId: previewJobId,
      pageNo: activePageNo || undefined,
      label: store.activeImportJob?.label || previewJobId.slice(0, 8),
    });
    syncPageStrip();
  }

  function setCenterMode(mode: "job" | "page"): void {
    centerMode = mode;
    previewWrap.classList.toggle("is-job-overview", mode === "job");
    previewWrap.classList.toggle("is-page-preview", mode === "page");
  }

  function showJobOverview(): void {
    pendingScrollPage = 0;
    activePageNo = 0;
    setCenterMode("job");
    emitPageNavState();
  }

  function syncPlaceholder(): void {
    previewPlaceholder.classList.toggle(
      "hidden",
      !!previewJobId || livePipeline.isArmed(),
    );
  }

  syncPlaceholder();

  function stopPreviewPoll(): void {
    if (previewJobUnsub) {
      previewJobUnsub();
      previewJobUnsub = null;
    }
  }

  function pageHasContent(body: HTMLElement | undefined): boolean {
    if (!body) return false;
    return !!body.querySelector(".lb-page, article, pre.import-mineru-md-draft");
  }

  function destroyViewport(): void {
    if (healTimer != null) {
      clearInterval(healTimer);
      healTimer = null;
    }
    viewport?.destroy();
    viewport = null;
  }

  function startHealLoop(): void {
    if (healTimer != null) return;
    healTimer = window.setInterval(() => {
      if (!previewJobId || !viewport) return;
      viewport.healVisible();
      // Also warm a few pages ahead of the active one.
      if (activePageNo > 0) {
        enqueueWarmPages([
          activePageNo - 1,
          activePageNo,
          activePageNo + 1,
          activePageNo + 2,
          activePageNo + 3,
        ]);
      }
    }, 400);
  }

  function scrollToPage(pageNo: number): boolean {
    if (pageNo < 1) return false;
    if (previewTotal > 0 && pageNo > previewTotal) return false;
    ensurePageShell(pageNo);
    pendingScrollPage = 0;
    activePageNo = pageNo;
    emitPageNavState();
    // Prioritize warming the jump target + neighbors.
    enqueueWarmPages([pageNo - 1, pageNo, pageNo + 1, pageNo + 2].filter((p) => p > 0));
    if (viewport) {
      viewport.scrollToPage(pageNo);
      const body = pageBodies.get(pageNo);
      if (body && !pageHasContent(body)) {
        viewport.invalidate(pageNo);
      }
    } else {
      const block = pageWraps.get(pageNo);
      if (!block) return false;
      // Instant align into previewPages (smooth + shell height churn → wrong page).
      const rootRect = previewPages.getBoundingClientRect();
      const blockRect = block.getBoundingClientRect();
      previewPages.scrollTop = Math.max(
        0,
        previewPages.scrollTop + (blockRect.top - rootRect.top),
      );
    }
    const block = pageWraps.get(pageNo);
    if (block) {
      block.classList.add("import-page-block--focus");
      window.setTimeout(() => block.classList.remove("import-page-block--focus"), 1200);
    }
    return true;
  }

  function findTocPageNo(): number {
    const toc = previewPages.querySelector<HTMLElement>(".lb-toc-wrap, .lb-toc");
    if (!toc) return 0;
    const wrap =
      toc.closest<HTMLElement>(".import-page-block") ||
      toc.closest<HTMLElement>("[data-page-no]");
    const fromWrap = Number(wrap?.dataset.pageNo || 0);
    if (fromWrap > 0) return fromWrap;
    const article = toc.closest<HTMLElement>(".lb-page");
    return Number(article?.getAttribute("data-page") || 0);
  }

  /** Mark footer page numbers as "back to TOC" links (works on already-rendered HTML). */
  function decoratePageNumbers(): void {
    const tocPageNo = findTocPageNo();
    pageBodies.forEach((body) => {
      const isTocPage = !!body.querySelector(".lb-toc-wrap, .lb-toc");
      body.querySelectorAll<HTMLElement>(".lb-page-number").forEach((el) => {
        if (isTocPage || tocPageNo <= 0) {
          el.classList.remove("lb-page-number--toc-link");
          el.removeAttribute("data-goto-page");
          el.removeAttribute("title");
          el.removeAttribute("role");
          el.removeAttribute("tabindex");
          return;
        }
        el.classList.add("lb-page-number--toc-link");
        el.title = "返回目录页";
        el.setAttribute("role", "link");
        el.tabIndex = 0;
        el.setAttribute("data-goto-page", String(tocPageNo));
      });
    });
  }

  function scrollToPrintPage(printPage: number): boolean {
    const article = previewPages.querySelector<HTMLElement>(
      `.lb-page[data-print-page="${printPage}"]`,
    );
    if (!article) return false;
    const wrap =
      article.closest<HTMLElement>(".import-page-block") ||
      article.closest<HTMLElement>("[data-page-no]") ||
      article;
    const pageNo = Number(wrap.dataset.pageNo || article.dataset.page || 0);
    if (pageNo > 0) return scrollToPage(pageNo);
    const top =
      wrap.getBoundingClientRect().top -
      previewPages.getBoundingClientRect().top +
      previewPages.scrollTop;
    previewPages.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
    return true;
  }

  async function scrollToPrintPageWhenReady(printPage: number): Promise<void> {
    for (let i = 0; i < 50; i++) {
      if (scrollToPrintPage(printPage)) return;
      if (previewJobId && i > 0 && i % 8 === 0) {
        try {
          const job = await corpus.importJob(previewJobId);
          await syncViewportFromJob(job);
        } catch {
          /* retry */
        }
      }
      await new Promise((r) => window.setTimeout(r, 80));
    }
  }

  // TOC row → jump to section; footer page number → back to TOC
  previewPages.addEventListener("click", (ev) => {
    const t = ev.target as HTMLElement | null;
    const pageNum = t?.closest?.<HTMLElement>(".lb-page-number--toc-link");
    if (pageNum && previewPages.contains(pageNum)) {
      ev.preventDefault();
      ev.stopPropagation();
      const pdfPage = Number(pageNum.getAttribute("data-goto-page") || findTocPageNo() || 0);
      if (pdfPage > 0) void scrollToPageWhenReady(pdfPage);
      return;
    }
    const row = t?.closest?.<HTMLElement>(
      ".lb-toc-row[data-goto-print-page], .lb-toc-row[data-goto-page], [data-goto-page], [data-goto-print-page]",
    );
    if (!row || !previewPages.contains(row)) return;
    // Avoid treating decorated page-number as a generic goto target twice
    if (row.classList.contains("lb-page-number")) return;
    ev.preventDefault();
    ev.stopPropagation();
    const pdfPage = Number(row.getAttribute("data-goto-page") || 0);
    const printPage = Number(row.getAttribute("data-goto-print-page") || 0);
    if (pdfPage > 0) {
      void scrollToPageWhenReady(pdfPage);
      return;
    }
    if (printPage > 0) {
      void scrollToPrintPageWhenReady(printPage);
    }
  });

  previewPages.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const t = ev.target as HTMLElement | null;
    if (
      t?.classList?.contains("lb-toc-row") ||
      t?.classList?.contains("lb-page-number--toc-link")
    ) {
      t.click();
      ev.preventDefault();
    }
  });

  async function scrollToPageWhenReady(pageNo: number): Promise<void> {
    setCenterMode("page");
    if (previewTotal > 0) {
      const vp = ensureViewport();
      vp.setPageCount(previewTotal);
    }
    await new Promise<void>((resolve) => {
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    });
    pendingScrollPage = pageNo;
    if (previewJobId && previewTotal < pageNo) {
      // Expand shell range if TOC/jump targets a page ahead of known total.
      previewTotal = pageNo;
      viewport?.setPageCount(pageNo);
      syncPageStrip();
    }
    if (pendingScrollPage === pageNo && scrollToPage(pageNo)) return;
    for (let i = 0; i < 40; i++) {
      if (pendingScrollPage !== pageNo) return;
      if (scrollToPage(pageNo)) return;
      await new Promise((r) => window.setTimeout(r, 80));
    }
  }

  pageStrip = mountStickyPageStrip({
    onGoto: (pageNo) => {
      void scrollToPageWhenReady(pageNo);
    },
  });
  pageStripHost.appendChild(pageStrip.el);

  function ensureOpenTextbookBtn(): void {
    // 旧版第二层工具条：已并入 import-preview-head，残留则清掉。
    previewWrap.querySelector(".import-open-textbook-bar")?.remove();
    let actions = compare.toolbar.querySelector(
      ".import-preview-book-actions",
    ) as HTMLElement | null;
    if (!previewTextbookId) {
      actions?.remove();
      return;
    }
    if (actions) return;
    actions = el("div", "import-preview-book-actions");
    const openBtn = el("button", "btn-secondary", "在教材中打开") as HTMLButtonElement;
    openBtn.type = "button";
    openBtn.title = "到教材树打开这一版（审核 / 发布仍走原流程）";
    openBtn.onclick = () => {
      if (previewTextbookId) void store.openInTextbook(previewTextbookId);
    };
    const finalizeBtn = el("button", "btn-danger", "定稿") as HTMLButtonElement;
    finalizeBtn.type = "button";
    finalizeBtn.title =
      "把这一版定为该教材唯一版本；同一本书的其他导入连数据删除（先预览再确认）";
    finalizeBtn.onclick = async () => {
      const jobId = previewJobId;
      if (!jobId) return;
      finalizeBtn.disabled = true;
      try {
        const { runFinalizeBook } = await import("./finalizeBook");
        if (await runFinalizeBook(jobId)) await refresh();
      } finally {
        finalizeBtn.disabled = false;
      }
    };
    actions.append(openBtn, finalizeBtn);
    compare.toolbar.appendChild(actions);
  }

  function setPendingHint(job: ImportJob): void {
    const meta = job.meta || {};
    const ready = Array.isArray(meta.pages_ready) ? meta.pages_ready.length : 0;
    const total =
      previewTotal ||
      Number(meta.total_pages || meta.ocr_total || meta.page_count || job.page_count || 0);
    const phase = String(meta.pipeline_phase || "");
    const ocr = String(job.progress || meta.progress || meta.ocr_progress || "");
    const escalate = Array.isArray(meta.layout_escalate_pages)
      ? meta.layout_escalate_pages.map(Number).filter((n) => n > 0)
      : [];
    if (TERMINAL.has(String(job.status || "")) && ready >= total && total > 0) {
      // 症状清单在 worker 里已算好，不开 AI 也要看得见
      if (symptomsLoadedFor !== job.id) {
        symptomsLoadedFor = job.id;
        void symptoms.load(job.id);
      }
      if (escalate.length > 0) {
        const head = escalate.slice(0, 12).join(", ");
        pendingBar.textContent =
          `待 Cursor 判定 ${escalate.length} 页：${head}` +
          (escalate.length > 12 ? "…" : "");
        pendingBar.classList.remove("hidden");
      } else {
        pendingBar.classList.add("hidden");
        pendingBar.textContent = "";
      }
      return;
    }
    const bits: string[] = [];
    if (ocr) bits.push(ocr.includes("/") ? `Cloud ${ocr}` : ocr);
    if (phase) bits.push(phase);
    if (total > 0) bits.push(`已出稿 ${ready}/${total}`);
    else if (ready > 0) bits.push(`已出稿 ${ready} 页`);
    else bits.push("等待首屏草稿…");
    if (escalate.length > 0) bits.push(`低置信 ${escalate.length} 页待 Cursor`);
    pendingBar.textContent = bits.join(" · ");
    pendingBar.classList.remove("hidden");
    ensureOpenTextbookBtn();
  }

  function ensurePageShell(pageNo: number): HTMLElement {
    const existing = pageWraps.get(pageNo);
    if (existing) return existing;

    const wrap = el("div", "import-page-block import-page-block--mineru is-shell");
    wrap.dataset.pageNo = String(pageNo);

    const tagRow = el("div", "import-page-tag");
    const title = el("span", "", `第 ${pageNo} 页`);
    const hint = el("span", "muted import-page-ai-hint", "双击本页或块 → 填入 AI 对话");
    tagRow.append(title, hint);

    const body = el("div", "import-preview-mineru is-unloaded");
    body.textContent = "准备加载…";
    const debugHost = el("div", "import-preview-debug-host");

    wrap.append(tagRow, body, debugHost);
    const existingCards = [
      ...previewPages.querySelectorAll<HTMLElement>(".import-page-block[data-page-no]"),
    ];
    const before = existingCards.find((c) => Number(c.dataset.pageNo || 0) > pageNo);
    // Compare mode nests cards inside rows — insert relative to the row, not the nested card.
    const beforeTop = before?.closest<HTMLElement>(".import-compare-row") || before;
    if (beforeTop && beforeTop.parentElement === previewPages) {
      previewPages.insertBefore(wrap, beforeTop);
    } else {
      previewPages.appendChild(wrap);
    }

    pageBodies.set(pageNo, body);
    pageWraps.set(pageNo, wrap);
    mountedPages.add(pageNo);
    compare.notifyShell(pageNo);

    const flashFocus = (elFlash: HTMLElement) => {
      elFlash.classList.add("is-ai-focus");
      window.setTimeout(() => elFlash.classList.remove("is-ai-focus"), 900);
    };

    wrap.addEventListener("dblclick", (ev) => {
      if (!previewJobId) return;
      const target = ev.target as HTMLElement | null;
      const blockEl = target?.closest?.("[data-block-id]") as HTMLElement | null;
      if (blockEl && body.contains(blockEl)) {
        ev.preventDefault();
        ev.stopPropagation();
        const blockId = blockEl.getAttribute("data-block-id") || "";
        if (!blockId) return;
        flashFocus(blockEl);
        emitFocusTarget(previewJobId, pageNo, blockId);
        return;
      }
      ev.preventDefault();
      flashFocus(wrap);
      emitFocusTarget(previewJobId, pageNo);
    });

    return wrap;
  }

  function injectPageHtml(body: HTMLElement, html: string): void {
    const trimmed = html.trim();
    body.classList.remove("is-unloaded");
    if (trimmed.includes("<") && trimmed.includes(">")) {
      body.innerHTML = sanitizeHtml(trimmed);
    } else {
      body.innerHTML = `<pre class="import-mineru-md-draft">${escapeText(trimmed)}</pre>`;
    }
    typesetTextbookMathIdle(body);
  }

  function unloadPageContent(pageNo: number, shell: HTMLElement): void {
    // Compare mode: keep HTML mounted — unload+stop-scroll leaves permanent blanks.
    if (compare.isEnabled()) return;
    const body = pageBodies.get(pageNo) || shell.querySelector<HTMLElement>(".import-preview-mineru");
    if (!body) return;
    // Prefer instant reinject from cache instead of blank placeholder.
    const cached = htmlCache.get(pageNo);
    if (cached?.trim()) {
      injectPageHtml(body, cached);
      shell.classList.remove("is-shell");
      loadedPages.add(pageNo);
      return;
    }
    body.replaceChildren();
    body.classList.add("is-unloaded");
    body.textContent = "准备加载…";
    shell.classList.add("is-shell");
    loadedPages.delete(pageNo);
  }

  function mountDebugIfNeeded(pageNo: number, shell: HTMLElement): void {
    if (!previewJobId || debugMounted.has(pageNo)) return;
    const debugHost = shell.querySelector<HTMLElement>(".import-preview-debug-host");
    if (debugHost) {
      mountCorpusPageDebugDrawer(debugHost, previewJobId, pageNo);
      debugMounted.add(pageNo);
    }
  }

  /** Fetch page HTML into htmlCache early (does not require shell in viewport). */
  async function warmHtmlCache(pageNo: number): Promise<void> {
    if (!previewJobId || pageNo < 1) return;
    if (htmlCache.has(pageNo) || warmInflight.has(pageNo)) return;
    const epoch = pageEpoch(pageNo);
    warmInflight.add(pageNo);
    try {
      const res = await corpus.getImportPageHtml(previewJobId, pageNo);
      if (pageEpoch(pageNo) !== epoch) return; // hard refresh won the race
      const html = String(res.html || "");
      const total = Number(res.total_pages || 0);
      if (total > 0 && total !== previewTotal) {
        previewTotal = total;
        viewport?.setPageCount(total);
        syncPageStrip();
      }
      if (!html.trim()) return;
      htmlCache.set(pageNo, html);
      readyPageSet.add(pageNo);
      const body = pageBodies.get(pageNo);
      if (body && !pageHasContent(body)) {
        viewport?.invalidate(pageNo);
      }
    } catch {
      /* transient — viewport load / later warm will retry */
    } finally {
      warmInflight.delete(pageNo);
      pumpWarmQueue();
    }
  }

  function pumpWarmQueue(): void {
    while (warmInflight.size < WARM_CONCURRENCY && warmQueue.length > 0) {
      const p = warmQueue.shift();
      if (p == null) break;
      if (htmlCache.has(p) || warmInflight.has(p)) continue;
      void warmHtmlCache(p);
    }
  }

  function enqueueWarmPages(pages: number[]): void {
    const center = activePageNo || 1;
    const uniq = [...new Set(pages.filter((p) => p > 0 && !htmlCache.has(p)))];
    uniq.sort((a, b) => Math.abs(a - center) - Math.abs(b - center));
    for (const p of uniq) {
      if (!warmQueue.includes(p)) warmQueue.push(p);
    }
    // Keep queue ordered by distance to active page.
    warmQueue.sort((a, b) => Math.abs(a - center) - Math.abs(b - center));
    pumpWarmQueue();
  }

  async function loadPageIntoShell(pageNo: number, shell: HTMLElement): Promise<boolean> {
    if (!previewJobId) return false;
    const body =
      pageBodies.get(pageNo) ||
      shell.querySelector<HTMLElement>(".import-preview-mineru");
    if (!body) return false;

    const applyHtml = (html: string): true => {
      injectPageHtml(body, html);
      shell.classList.remove("is-shell");
      mountDebugIfNeeded(pageNo, shell);
      loadedPages.add(pageNo);
      decoratePageNumbers();
      return true;
    };

    const cached = htmlCache.get(pageNo);
    if (cached?.trim()) {
      return applyHtml(cached);
    }

    body.classList.add("is-unloaded");
    body.textContent = readyPageSet.has(pageNo) ? "加载中…" : "等待出稿…";

    try {
      // Even if meta.pages_ready lags, try the HTML API — blank "准备加载" is worse.
      const res = await corpus.getImportPageHtml(previewJobId, pageNo);
      const html = String(res.html || "");
      const total = Number(res.total_pages || 0);
      if (total > 0 && total !== previewTotal) {
        previewTotal = total;
        viewport?.setPageCount(total);
        syncPageStrip();
      }
      if (!html.trim()) {
        body.classList.add("is-unloaded");
        body.textContent = "等待出稿…";
        return false;
      }
      htmlCache.set(pageNo, html);
      readyPageSet.add(pageNo);
      return applyHtml(html);
    } catch {
      body.classList.add("is-unloaded");
      body.textContent = "加载失败，将自动重试…";
      return false;
    }
  }

  function ensureViewport(): PageViewportHandle {
    if (viewport) return viewport;
    viewport = createPageViewportController({
      root: previewPages,
      pageCount: previewTotal,
      radius: VIEWPORT_RADIUS,
      keepRadius: VIEWPORT_KEEP,
      ensureShell: ensurePageShell,
      loadPage: loadPageIntoShell,
      unloadPage: unloadPageContent,
      shouldUnload: () => !compare.isEnabled(),
      isFilled: (pageNo, shell) => {
        const body =
          pageBodies.get(pageNo) ||
          shell.querySelector<HTMLElement>(".import-preview-mineru");
        return pageHasContent(body || undefined);
      },
      onActivePage: (pageNo) => {
        if (pageNo > 0 && pageNo !== activePageNo) {
          activePageNo = pageNo;
          emitPageNavState();
        }
        // Keep neighbors warm ahead of scroll.
        const near: number[] = [];
        for (let d = -VIEWPORT_RADIUS; d <= VIEWPORT_RADIUS + 3; d++) {
          near.push(pageNo + d);
        }
        enqueueWarmPages(near);
        viewport?.healVisible();
      },
    });
    startHealLoop();
    return viewport;
  }

  /**
   * 彻底刷新当前页：优先 POST rerender（从最新 IR 重渲染并写回草稿），
   * 再强制注入 DOM。仅 GET 草稿会看到过期 draft_chunk，不如浏览器整页刷新。
   */
  async function softRefreshMountedPage(pageNo: number): Promise<void> {
    if (!previewJobId || pageNo <= 0) return;
    const epoch = bumpPageEpoch(pageNo);
    htmlCache.delete(pageNo);
    warmQueue = warmQueue.filter((p) => p !== pageNo);
    try {
      let html = "";
      try {
        const rerendered = await corpus.rerenderImportPage(previewJobId, pageNo);
        html = String(rerendered.html || "").trim();
      } catch (rerenderErr) {
        // No IR yet / job still streaming — fall back to bust-cached GET.
        console.warn("MinerU rerender 不可用，回退 GET", pageNo, rerenderErr);
        const res = await corpus.getImportPageHtml(previewJobId, pageNo, {
          bustCache: true,
        });
        html = String(res.html || "").trim();
      }
      if (pageEpoch(pageNo) !== epoch) return;
      if (!html) return;
      htmlCache.set(pageNo, html);
      readyPageSet.add(pageNo);
      const shell = pageWraps.get(pageNo) || ensurePageShell(pageNo);
      const body =
        pageBodies.get(pageNo) ||
        shell.querySelector<HTMLElement>(".import-preview-mineru");
      if (body) {
        injectPageHtml(body, html);
        shell.classList.remove("is-shell");
        loadedPages.add(pageNo);
        decoratePageNumbers();
        // Remount debug drawer so artifacts match the new HTML.
        debugMounted.delete(pageNo);
        const host = shell.querySelector<HTMLElement>(".import-preview-debug-host");
        if (host) host.replaceChildren();
        mountDebugIfNeeded(pageNo, shell);
      } else {
        viewport?.invalidate(pageNo);
      }
    } catch (e) {
      console.warn("MinerU 页软刷新失败", pageNo, e);
      throw e;
    }
  }

  function escapeText(s: string): string {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function readyPagesFromJob(job: ImportJob): number[] {
    const meta = job.meta || {};
    const ready = Array.isArray(meta.pages_ready)
      ? meta.pages_ready.map(Number).filter((n) => n > 0)
      : [];
    return [...new Set(ready)].sort((a, b) => a - b);
  }

  /** Sync shell count + ready set; never bulk-inject HTML. */
  async function syncViewportFromJob(job: ImportJob): Promise<void> {
    const meta = job.meta || {};
    const total = Number(
      meta.total_pages || meta.ocr_total || meta.page_count || job.page_count || 0,
    );
    const ready = readyPagesFromJob(job);
    const maxReady = ready.length ? Math.max(...ready) : 0;
    const nextTotal = Math.max(total, maxReady, previewTotal);
    if (nextTotal > 0) previewTotal = nextTotal;

    const newlyReady: number[] = [];
    for (const p of ready) {
      if (!readyPageSet.has(p)) newlyReady.push(p);
      readyPageSet.add(p);
    }
    // Terminal with known total: allow fetch for any page in 1..N
    if (TERMINAL.has(String(job.status || "")) && previewTotal > 0) {
      for (let p = 1; p <= previewTotal; p++) {
        if (!readyPageSet.has(p)) newlyReady.push(p);
        readyPageSet.add(p);
      }
    }

    setPendingHint(job);
    emitPageNavState();

    // 书名总览只看流水线 / 症状，不预热全书 HTML，否则 150 页会把主线程打满。
    if (centerMode !== "page") {
      return;
    }

    const vp = ensureViewport();
    vp.setPageCount(previewTotal);

    for (const p of newlyReady) vp.invalidate(p);
    vp.refresh();

    const center = pendingScrollPage || activePageNo || 1;
    const near: number[] = [];
    for (let d = -VIEWPORT_KEEP; d <= VIEWPORT_KEEP; d++) {
      const p = center + d;
      if (p > 0 && (previewTotal <= 0 || p <= previewTotal)) near.push(p);
    }
    enqueueWarmPages(near);

    if (pendingScrollPage > 0) scrollToPage(pendingScrollPage);
    if (!activePageNo && previewTotal > 0) {
      activePageNo = 1;
    }
  }

  async function onPreviewJobUpdate(job: ImportJob): Promise<void> {
    if (!previewJobId || job.id !== previewJobId) return;
    const tb = String((job as ImportJob & { textbook_node_id?: string }).textbook_node_id || "").trim();
    if (tb) {
      previewTextbookId = tb;
      ensureOpenTextbookBtn();
    }
    livePipeline.applyJob(job);
    await syncViewportFromJob(job);
    const terminal = TERMINAL.has(String(job.status || ""));
    const ready = readyPagesFromJob(job).length;
    if (terminal && previewTotal > 0 && ready >= previewTotal) {
      stopPreviewPoll();
      setPendingHint(job);
    } else if (terminal && previewTotal > 0) {
      // Keep a light poll until pages_ready catches up; viewport loads on demand.
      setPendingHint(job);
    }
  }

  async function tickPreviewJob(): Promise<void> {
    if (!previewJobId) return;
    try {
      const job = await corpus.importJob(previewJobId);
      store.setImportJob(job);
      await onPreviewJobUpdate(job);
    } catch {
      /* transient */
    }
  }

  function startPreviewPoll(jobId: string): void {
    stopPreviewPoll();
    destroyViewport();
    previewJobId = jobId;
    compare.bindJob(jobId);
    void import("./activeMinerUJob").then(({ persistActiveMinerUJob }) => {
      persistActiveMinerUJob(jobId);
    });
    previewTextbookId = "";
    previewTotal = 0;
    mountedPages = new Set();
    loadedPages = new Set();
    pageBodies = new Map();
    pageWraps = new Map();
    htmlCache = new Map();
    readyPageSet = new Set();
    debugMounted = new Set();
    pageFetchEpoch.clear();
    warmQueue = [];
    warmInflight.clear();
    activePageNo = 0;
    previewPages.innerHTML = "";
    pendingBar.textContent = "连接任务…";
    pendingBar.classList.remove("hidden");
    symptomsLoadedFor = "";
    symptoms.clear();
    livePipeline.bindJob(jobId);
    if (symptomWatchStop) symptomWatchStop();
    symptomWatchStop = startSymptomWatch(livePipeline, jobId);
    syncPlaceholder();
    emitPageNavState();
    scheduleMinerUTreeRefresh(jobId);
    // Single Data Plane observer owns importJob HTTP; panel only reacts + expands shells.
    startImportProgressPolling(jobId);
    previewJobUnsub = store.subscribeSelector(
      (s) => s.activeImportJob,
      (job) => {
        if (job && job.id === previewJobId) void onPreviewJobUpdate(job);
      },
    );
    void tickPreviewJob();
  }

  function openMinerUPreview(jobId: string, pageNo?: number): void {
    store.showImportPreview();
    const wantPage = pageNo && pageNo > 0 ? pageNo : 0;
    pendingScrollPage = wantPage;

    // Same job already mounted: switch 总览 / 页预览. Do not re-link CSS.
    if (previewJobId === jobId && mountedPages.size > 0) {
      if (wantPage) void scrollToPageWhenReady(wantPage);
      else showJobOverview();
      return;
    }

    ensureCss();
    ensureCriticalCss(previewRoot);
    startPreviewPoll(jobId);
    void restoreAiForJob(jobId);
    if (wantPage) void scrollToPageWhenReady(wantPage);
    else showJobOverview();
  }

  document.addEventListener("studio:mineru-preview", ((e: CustomEvent<{
    jobId: string;
    pageNo?: number;
  }>) => {
    if (e.detail?.jobId) openMinerUPreview(e.detail.jobId, e.detail.pageNo);
  }) as EventListener);

  studioBus.on("studio:mineru-pipeline-tick", (tick) => {
    const jid = String(tick.job_id || "").trim();
    if (jid && !previewJobId) openMinerUPreview(jid);
    if (jid && previewJobId && jid !== previewJobId) return;
    livePipeline.applyTick(tick);
  });

  document.addEventListener(MINERU_PAGE_SOFT_REFRESH, ((e: CustomEvent<{
    jobId?: string;
    pageNo?: number;
  }>) => {
    const jid = String(e.detail?.jobId || "").trim();
    const pn = Number(e.detail?.pageNo) || 0;
    if (!jid || jid !== previewJobId || pn <= 0) return;
    void softRefreshMountedPage(pn);
  }) as EventListener);

  function clearPreview(): void {
    // 仅清中栏 DOM/轮询；不删 localStorage 活跃任务（刷新后要能恢复三栏）
    stopPreviewPoll();
    destroyViewport();
    previewJobId = "";
    compare.bindJob(null);
    compare.setEnabled(false);
    previewTextbookId = "";
    previewTotal = 0;
    pendingScrollPage = 0;
    mountedPages = new Set();
    loadedPages = new Set();
    pageBodies = new Map();
    pageWraps = new Map();
    htmlCache = new Map();
    readyPageSet = new Set();
    debugMounted = new Set();
    pageFetchEpoch.clear();
    warmQueue = [];
    warmInflight.clear();
    activePageNo = 0;
    previewPages.innerHTML = "";
    pendingBar.textContent = "";
    pendingBar.classList.add("hidden");
    symptomsLoadedFor = "";
    symptoms.clear();
    if (symptomWatchStop) {
      symptomWatchStop();
      symptomWatchStop = null;
    }
    livePipeline.clear();
    centerMode = "job";
    previewWrap.classList.remove("is-job-overview", "is-page-preview");
    syncPlaceholder();
    emitPageNavState();
  }

  document.addEventListener("studio:import-hub", () => {
    clearPreview();
  });

  document.addEventListener("studio:mineru-import-deleted", ((e: CustomEvent<{ jobId: string }>) => {
    const jid = e.detail?.jobId || "";
    if (jid && jid === previewJobId) clearPreview();
    if (jid) {
      void import("../CopilotPanel").then(({ onImportJobDeleted }) => {
        onImportJobDeleted(jid);
      });
    }
  }) as EventListener);

  startBtn.onclick = async () => {
    const f = fileInput.files?.[0];
    if (!f) {
      msg.textContent = "请选择 PDF 文件";
      return;
    }
    const eid = editionSelect.value.trim();
    if (!eid) {
      msg.textContent = "请选择教材解析方案";
      return;
    }
    if (knownBook.mode() === "open_previous") {
      const prev = knownBook.sourceJobId();
      if (prev) {
        store.showImportPreview();
        openMinerUPreview(prev);
        msg.textContent = `已打开上次的导入任务 ${prev.slice(0, 8)}`;
        return;
      }
    }
    startBtn.disabled = true;
    msg.textContent = "上传中…";
    try {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("subject", "math");
      fd.append("grade_band", "hs");
      fd.append("label", resolveLabel(f));
      fd.append("parse_engine", "mineru_cloud");
      fd.append("edition_id", eid);
      const pl = Number(pageLimitInput.value || 0);
      if (pl > 0) fd.append("page_limit", String(pl));
      const inheritFrom = knownBook.mode() === "inherit" ? knownBook.sourceJobId() : "";
      if (inheritFrom) fd.append("inherit_from_job_id", inheritFrom);
      const out = await importPdf(fd);
      msg.textContent = `已入队：${out.id}` + (inheritFrom ? `（${knownBook.summary()}）` : "");
      if (out.id) {
        startImportProgressPolling(out.id);
        store.showImportPreview();
        openMinerUPreview(out.id);
      }
    } catch (e) {
      msg.textContent = `失败：${(e as Error).message}`;
    } finally {
      startBtn.disabled = false;
    }
  };

  async function refresh(): Promise<void> {
    if (previewJobId) await tickPreviewJob();
  }

  /** 供外层启动时恢复：中栏预览 + 右栏 AI（左栏由树刷新高亮）。 */
  async function restoreSessionForJob(jobId: string): Promise<void> {
    const jid = String(jobId || "").trim();
    if (!jid) return;
    openMinerUPreview(jid);
  }

  return {
    setupRoot,
    previewRoot,
    refresh: () => void refresh(),
    restoreSessionForJob,
  };
}
