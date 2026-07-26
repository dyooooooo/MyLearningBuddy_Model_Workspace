// MinerU line import panel (corpus). Fully isolated from the 35B panel.
//
// Preview mounts pages only when draft/IR is ready (like B35), not all 1..N
// empty waiting cards. Ops: reprocess / debug / feedback via corpus APIs.

import { corpus, importPdf, probePdf } from "../../api/corpus";
import type { ImportJob } from "../../api/types";
import {
  startImportProgressPolling,
} from "../../services/importProgress";
import { IMPORT_JOB_TERMINAL } from "../../services/importJobObserver";
import { store } from "../../state/store";
import { sanitizeHtml, TEXTBOOK_CSS_HREF, TEXTBOOK_CSS_VERSION } from "../../util/html-sanitize";
import { typesetTextbookMathIdle } from "../../util/math-render";
import { el } from "../../util/dom";
import { mountCorpusPageDebugDrawer } from "./CorpusPageDebugDrawer";
import { openCorpusFeedbackDialog } from "./CorpusFeedbackDialog";
import { openCorpusRerecognizeDialog, formatApiError } from "./CorpusRerecognizeDialog";
import {
  emitMinerUPreviewState,
  MINERU_PAGE_SOFT_REFRESH,
  scheduleMinerUTreeRefresh,
} from "./MinerUImportTree";
import { mountStickyPageStrip } from "./StickyPageStrip";
import {
  clearWorkflowSteps,
  pushWorkflowStep,
  setWorkflowFocus,
  setWorkflowResult,
  withWorkflowStep,
} from "../../state/workflowDebug";
import type { PanelHandle } from "./types";

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
  editionSelect.title = "教材解析方案（必选）：决定分栏识别与校对规则；AI 分段导入也须先选方案";
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
  fileInput.onchange = async () => {
    probeHint.textContent = "";
    const f = fileInput.files?.[0];
    if (!f) return;
    // Copilot 会话进行中换/补选 PDF：自动预上传并把 minio_key 注入会话（Agent 自己接管导入）
    void notifyCopilotNewPdf(f);
    try {
      const info = await probePdf(f);
      probeHint.textContent = `已探测 · 共 ${info.page_count ?? info.pages ?? "?"} 页`;
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
  const previewPages = el("div", "import-preview-pages");
  // 页码条稍后挂上（等 scrollToPageWhenReady 定义），避免打包器 TDZ 问题
  const pageStripHost = el("div", "studio-sticky-page-strip-host");
  previewWrap.append(previewPlaceholder, pendingBar, pageStripHost, previewPages);
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
  aiBtn.textContent = "AI 分段导入";
  aiBtn.title =
    "须先选教材方案与 PDF：MinerU 每批 20 页识别 → 右栏 Agent 质检；中栏看结果";
  aiBtn.onclick = () => {
    void (async () => {
      const eid = editionSelect.value.trim();
      if (!eid || eid.startsWith("__")) {
        msg.textContent = "请先选择教材方案，再点 AI 分段导入";
        return;
      }
      const f = fileInput.files?.[0];
      if (!f) {
        msg.textContent = "请先选择 PDF 文件";
        return;
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
        // 刻意不带 job_id / import_job_id：等 AI 真正 create 任务后再绑定
        const { getImportCopilotHost, showImportCopilotTab } = await import("../Inspector");
        const { openCopilotPanel, closeCopilotPanel } = await import("../CopilotPanel");
        const host = getImportCopilotHost() || copilotHost;
        showImportCopilotTab();
        closeCopilotPanel(host);
        openCopilotPanel({ kind: "import_advice", host, payload, title: "AI 分段导入" });
        msg.textContent =
          "右栏已开新对话；首批任务创建后将自动打开中栏预览并刷新左栏";
      } catch (e) {
        msg.textContent = `AI 启动失败：${e instanceof Error ? e.message : String(e)}`;
      } finally {
        aiBtn.disabled = false;
      }
    })();
  };

  const actions = el("div", "import-field import-field--actions");
  actions.append(startBtn, aiBtn, msg);

  grid.append(fileField, labelField, pageField, editionWrap, actions);
  form.append(formHead, grid);

  async function restoreAiForJob(jobId: string): Promise<void> {
    const jid = String(jobId || "").trim();
    if (!jid) return;
    try {
      const { getImportCopilotHost, showImportCopilotTab } = await import("../Inspector");
      const { tryRestoreCopilotPanel } = await import("../CopilotPanel");
      const host = getImportCopilotHost() || copilotHost;
      const bound = host.getAttribute("data-copilot-job") || "";
      const ok = await tryRestoreCopilotPanel({
        kind: "import_advice",
        host,
        title: "AI 分段导入",
        jobId: jid,
        force: !!bound && bound !== jid,
      });
      if (ok) showImportCopilotTab();
    } catch (e) {
      console.warn("恢复 Copilot 会话失败", e);
    }
  }

  let previewJobId = "";
  let previewTextbookId = "";
  let previewTotal = 0;
  /** Cards mounted in DOM (may still show「加载中」). */
  let mountedPages = new Set<number>();
  /** Pages that successfully received HTML. */
  let loadedPages = new Set<number>();
  let pageBodies = new Map<number, HTMLElement>();
  let previewJobUnsub: (() => void) | null = null;
  let loadingPages = new Set<number>();
  let pendingScrollPage = 0;
  let terminalCatchUpPasses = 0;
  let activePageNo = 0;
  let pageNavObserver: IntersectionObserver | null = null;
  const LOAD_CONCURRENCY = 4;
  const MAX_TERMINAL_CATCHUP = 8;
  let pageStrip: ReturnType<typeof mountStickyPageStrip> | null = null;

  function syncPageStrip(): void {
    if (!pageStrip) return;
    if (!previewJobId) {
      pageStrip.setPages([]);
      return;
    }
    const pages = [...mountedPages].filter((n) => n > 0).sort((a, b) => a - b);
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

  function bindPageNavObserver(): void {
    pageNavObserver?.disconnect();
    pageNavObserver = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        const top = visible[0]?.target as HTMLElement | undefined;
        const pageNo = Number(top?.dataset.pageNo || 0);
        if (pageNo > 0 && pageNo !== activePageNo) {
          activePageNo = pageNo;
          emitPageNavState();
        }
      },
      { root: previewPages, threshold: [0.2, 0.45, 0.7] },
    );
    previewPages.querySelectorAll<HTMLElement>("[data-page-no]").forEach((n) => {
      pageNavObserver?.observe(n);
    });
  }

  function syncPlaceholder(): void {
    previewPlaceholder.classList.toggle("hidden", !!previewJobId);
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

  function scrollToPage(pageNo: number): boolean {
    const block = previewPages.querySelector<HTMLElement>(`[data-page-no="${pageNo}"]`);
    if (!block) return false;
    pendingScrollPage = 0;
    activePageNo = pageNo;
    emitPageNavState();
    const top =
      block.getBoundingClientRect().top -
      previewPages.getBoundingClientRect().top +
      previewPages.scrollTop;
    previewPages.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
    block.classList.add("import-page-block--focus");
    window.setTimeout(() => block.classList.remove("import-page-block--focus"), 1200);
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
          await appendReadyPages(job);
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
    pendingScrollPage = pageNo;
    // Jump target first — don't wait for full-book poll when left-nav clicks a page.
    if (previewJobId) {
      try {
        await loadReadyPage(pageNo);
      } catch {
        /* retry below */
      }
      if (pendingScrollPage === pageNo && scrollToPage(pageNo)) return;
    }
    for (let i = 0; i < 80; i++) {
      if (pendingScrollPage !== pageNo) return;
      if (scrollToPage(pageNo)) return;
      if (previewJobId && i > 0 && i % 8 === 0) {
        try {
          await loadReadyPage(pageNo);
          const job = await corpus.importJob(previewJobId);
          await appendReadyPages(job);
        } catch {
          /* retry */
        }
      }
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
    let bar = previewWrap.querySelector(".import-open-textbook-bar") as HTMLElement | null;
    if (!previewTextbookId) {
      bar?.remove();
      return;
    }
    if (!bar) {
      bar = el("div", "import-open-textbook-bar");
      const btn = el("button", "btn-primary", "在教材中打开（定稿）") as HTMLButtonElement;
      btn.type = "button";
      btn.onclick = () => {
        if (previewTextbookId) void store.openInTextbook(previewTextbookId);
      };
      bar.append(
        el("span", "muted", "页预览仅校对。定稿、审核、发布请到教材树。"),
        btn,
      );
      previewWrap.insertBefore(bar, previewPages);
    }
  }

  function setPendingHint(job: ImportJob): void {
    const meta = job.meta || {};
    const ready = Array.isArray(meta.pages_ready) ? meta.pages_ready.length : 0;
    const total =
      previewTotal ||
      Number(meta.total_pages || meta.ocr_total || meta.page_count || job.page_count || 0);
    const phase = String(meta.pipeline_phase || "");
    const ocr = String(job.progress || meta.progress || meta.ocr_progress || "");
    if (TERMINAL.has(String(job.status || "")) && ready >= total && total > 0) {
      pendingBar.classList.add("hidden");
      pendingBar.textContent = "";
      return;
    }
    const bits: string[] = [];
    if (ocr) bits.push(ocr.includes("/") ? `Cloud ${ocr}` : ocr);
    if (phase) bits.push(phase);
    if (total > 0) bits.push(`已出稿 ${ready}/${total}`);
    else if (ready > 0) bits.push(`已出稿 ${ready} 页`);
    else bits.push("等待首屏草稿…");
    pendingBar.textContent = bits.join(" · ");
    pendingBar.classList.remove("hidden");
    ensureOpenTextbookBtn();
  }

  function mountPageCard(pageNo: number): HTMLElement {
    const wrap = el("div", "import-page-block import-page-block--mineru");
    wrap.dataset.pageNo = String(pageNo);

    const tagRow = el("div", "import-page-tag");
    const title = el("span", "", `第 ${pageNo} 页`);
    const actions = el("span", "import-page-tag-actions");
    const mineruBtn = el("button", "btn-link", "重新识别") as HTMLButtonElement;
    const contentBtn = el("button", "btn-link", "内容校正") as HTMLButtonElement;
    const suggestBtn = el("button", "btn-link", "建议重识别") as HTMLButtonElement;
    const feedbackBtn = el("button", "btn-link", "反馈问题") as HTMLButtonElement;
    mineruBtn.type = "button";
    contentBtn.type = "button";
    suggestBtn.type = "button";
    feedbackBtn.type = "button";
    mineruBtn.title =
      "恢复本页 MinerU 结构化基线（首次会写入快照；之后不被 35B 覆盖）";
    contentBtn.title = "一键校正本页文字/公式（不改版面结构，无需输入）";
    suggestBtn.title =
      "打开对话框：改内容（保结构）或重建版面并优选；不满意可点「重新识别」回滚";
    const aiBtn = el("button", "btn-link", "AI 助手") as HTMLButtonElement;
    aiBtn.type = "button";
    aiBtn.title = "让 Copilot 分诊本页问题：单页重识别 / 改方案 / 改 prompt / 改代码";
    // Copilot 内嵌面板：出现在本页卡片正下方（中栏内，不弹层）
    const pageCopilotHost = el("div", "copilot-host");
    aiBtn.onclick = () => {
      void import("../CopilotPanel").then(({ openCopilotPanel }) => {
        openCopilotPanel({
          kind: "page_assist",
          host: pageCopilotHost,
          payload: { job_id: previewJobId, page_no: pageNo },
          title: `AI 助手 · 第 ${pageNo} 页`,
        });
      });
    };
    const more = document.createElement("details");
    more.className = "import-page-more";
    const sum = document.createElement("summary");
    sum.textContent = "更多";
    more.append(sum, mineruBtn, suggestBtn);
    actions.append(contentBtn, aiBtn, feedbackBtn, more);
    tagRow.append(title, actions);

    const body = el("div", "import-preview-mineru");
    body.textContent = "加载中…";
    const debugHost = el("div", "import-preview-debug-host");

    wrap.append(tagRow, body, pageCopilotHost, debugHost);
    // Keep DOM in page-number order (parallel loads must not scramble scroll targets).
    const existing = [
      ...previewPages.querySelectorAll<HTMLElement>(".import-page-block[data-page-no]"),
    ];
    const before = existing.find((c) => Number(c.dataset.pageNo || 0) > pageNo);
    if (before) previewPages.insertBefore(wrap, before);
    else previewPages.appendChild(wrap);
    pageBodies.set(pageNo, body);
    mountedPages.add(pageNo);
    if (previewJobId) scheduleMinerUTreeRefresh(previewJobId);

    mountCorpusPageDebugDrawer(debugHost, previewJobId, pageNo);

    const applyPageHtml = (html: string) => {
      body.innerHTML = sanitizeHtml(html);
      typesetTextbookMathIdle(body);
      decoratePageNumbers();
      loadedPages.add(pageNo);
    };

    // MinerU restore — roll back to frozen MinerU baseline (never overwritten by 35B).
    mineruBtn.onclick = async () => {
      if (mineruBtn.disabled) return;
      mineruBtn.disabled = true;
      const prev = mineruBtn.textContent || "重新识别";
      mineruBtn.textContent = "MinerU 恢复中…";
      setWorkflowFocus({
        line: "mineru",
        jobId: previewJobId,
        pageNo,
        label: `第 ${pageNo} 页 · MinerU 重新识别`,
      });
      clearWorkflowSteps();
      pushWorkflowStep("info", `第 ${pageNo} 页恢复 MinerU 基线`);
      try {
        const out = await withWorkflowStep("调用 restore_mineru", () =>
          corpus.reprocessPage(previewJobId, pageNo, { mode: "restore_mineru" }),
        );
        setWorkflowResult({
          mode: out?.mode,
          restored_mineru_baseline: out?.restored_mineru_baseline,
          mineru_baseline_exists: out?.mineru_baseline_exists,
          effective_changes: out?.effective_changes,
          finding_count: out?.finding_count,
          persisted: out?.persisted,
        });
        pushWorkflowStep(
          "ok",
          "MinerU 基线已恢复",
          `changes=${out?.effective_changes ?? "?"} · baseline=${out?.mineru_baseline_exists ? "yes" : "seeded"}`,
        );
        const res = await withWorkflowStep("拉取更新后 HTML", () =>
          corpus.getImportPageHtml(previewJobId, pageNo),
        );
        applyPageHtml(String(res.html || ""));
        mineruBtn.textContent = "已恢复";
        window.setTimeout(() => {
          mineruBtn.textContent = prev;
          mineruBtn.disabled = false;
        }, 1000);
      } catch (e) {
        pushWorkflowStep("error", "MinerU 恢复失败", (e as Error).message);
        mineruBtn.textContent = "失败";
        mineruBtn.title = (e as Error).message;
        window.setTimeout(() => {
          mineruBtn.textContent = prev;
          mineruBtn.title =
            "恢复本页 MinerU 结构化基线（首次会写入快照；之后不被 35B 覆盖）";
          mineruBtn.disabled = false;
        }, 2000);
      }
    };

    // One-click content correct — no dialog, topology frozen.
    contentBtn.onclick = async () => {
      if (contentBtn.disabled) return;
      contentBtn.disabled = true;
      const prev = contentBtn.textContent || "内容校正";
      contentBtn.textContent = "内容校正中…";
      setWorkflowFocus({
        line: "mineru",
        jobId: previewJobId,
        pageNo,
        label: `第 ${pageNo} 页 · 内容校正`,
      });
      clearWorkflowSteps();
      pushWorkflowStep("info", `第 ${pageNo} 页内容校正`);
      try {
        const out = await withWorkflowStep("调用 35B content_correct", () =>
          corpus.reprocessPage(previewJobId, pageNo, { mode: "content_correct" }),
        );
        setWorkflowResult({
          mode: out?.mode,
          refine_patches: out?.refine_patches,
          refine_patch_preview: out?.refine_patch_preview,
          effective_changes: out?.effective_changes,
          noop: out?.noop,
          finding_count: out?.finding_count,
          correction_id: out?.correction_id,
          fingerprint_hash: out?.fingerprint_hash,
          persisted: out?.persisted,
        });
        pushWorkflowStep(
          "ok",
          out?.noop ? "无实质变更" : "内容校正完成",
          `patches=${out?.refine_patches ?? "?"} · changes=${out?.effective_changes ?? "?"}`,
        );
        const res = await withWorkflowStep("拉取更新后 HTML", () =>
          corpus.getImportPageHtml(previewJobId, pageNo),
        );
        applyPageHtml(String(res.html || ""));
        contentBtn.textContent = out?.noop ? "无变更" : "已更新";
        window.setTimeout(() => {
          contentBtn.textContent = prev;
          contentBtn.disabled = false;
        }, 1000);
      } catch (e) {
        pushWorkflowStep("error", "内容校正失败", (e as Error).message);
        contentBtn.textContent = "失败";
        contentBtn.title = (e as Error).message;
        window.setTimeout(() => {
          contentBtn.textContent = prev;
          contentBtn.title = "一键校正本页文字/公式（不改版面结构，无需输入）";
          contentBtn.disabled = false;
        }, 2000);
      }
    };

    // Dialog flow — structured context + user suggestion.
    suggestBtn.onclick = () => {
      openCorpusRerecognizeDialog({
        jobId: previewJobId,
        pageNo,
        onDone: (html) => {
          if (html) applyPageHtml(html);
        },
      });
    };

    body.addEventListener("click", (ev) => {
      const target = ev.target as HTMLElement | null;
      const blockEl = target?.closest?.("[data-block-id]") as HTMLElement | null;
      if (!blockEl) return;
      const blockId = blockEl.getAttribute("data-block-id") || "";
      if (!blockId) return;

      // Shift+click → dialog with suggestion; plain click → original one-shot.
      if (ev.shiftKey) {
        openCorpusRerecognizeDialog({
          jobId: previewJobId,
          pageNo,
          blockId,
          onDone: (html) => {
            if (html) applyPageHtml(html);
          },
        });
        return;
      }

      if (!confirm(`用 35B 重识别此块？\n${blockId}\n\n（按住 Shift 点击可打开建议对话框）`)) {
        return;
      }
      void (async () => {
        setWorkflowFocus({
          line: "mineru",
          jobId: previewJobId,
          pageNo,
          blockId,
          label: `第 ${pageNo} 页 · 块 ${blockId}`,
        });
        clearWorkflowSteps();
        pushWorkflowStep("info", "块一键重识别", blockId);
        try {
          const out = await withWorkflowStep("调用 35B refine_block", () =>
            corpus.refineBlock(previewJobId, pageNo, blockId),
          );
          setWorkflowResult({
            mode: out?.mode,
            block_id: blockId,
            refine_patches: out?.refine_patches,
            refine_patch_preview: out?.refine_patch_preview,
            persisted: out?.persisted,
            correction_id: out?.correction_id,
          });
          const res = await withWorkflowStep("拉取更新后 HTML", () =>
            corpus.getImportPageHtml(previewJobId, pageNo),
          );
          applyPageHtml(String(res.html || ""));
        } catch (e) {
          pushWorkflowStep("error", "块重识别失败", formatApiError(e));
          alert(`块重识别失败：${formatApiError(e)}`);
        }
      })();
    });

    feedbackBtn.onclick = () => {
      openCorpusFeedbackDialog({
        jobId: previewJobId,
        pageNo,
        pageEl: body,
      });
    };

    return wrap;
  }

  async function loadReadyPage(pageNo: number): Promise<void> {
    if (!previewJobId || loadingPages.has(pageNo)) return;
    if (loadedPages.has(pageNo) && pageHasContent(pageBodies.get(pageNo))) return;
    loadingPages.add(pageNo);
    try {
      if (!mountedPages.has(pageNo)) mountPageCard(pageNo);
      const body = pageBodies.get(pageNo);
      if (!body) return;
      const res = await corpus.getImportPageHtml(previewJobId, pageNo);
      const html = String(res.html || "");
      const total = Number(res.total_pages || 0);
      if (total > 0) previewTotal = total;
      if (!html.trim()) {
        // Empty response — keep card for retry; do not mark loaded.
        body.textContent = "等待出稿…";
        return;
      }
      if (html.includes("<") && html.includes(">")) {
        body.innerHTML = sanitizeHtml(html);
      } else {
        body.innerHTML = `<pre class="import-mineru-md-draft">${escapeText(html)}</pre>`;
      }
      typesetTextbookMathIdle(body);
      decoratePageNumbers();
      loadedPages.add(pageNo);
    } catch {
      // Not ready yet — leave card for next poll/retry
      const body = pageBodies.get(pageNo);
      if (body && !pageHasContent(body)) {
        body.textContent = "等待出稿…";
      }
    } finally {
      loadingPages.delete(pageNo);
    }
  }

  /**
   * IR / patch 后的中栏刷新：已挂载页原地换 HTML，不拆卡片、不清「加载中」、不抢滚动。
   * 先在离屏节点 typeset，再一次性 replaceChildren，避免闪白。
   */
  async function softRefreshMountedPage(pageNo: number): Promise<void> {
    if (!previewJobId || pageNo <= 0) return;
    const body = pageBodies.get(pageNo);
    if (!body || !mountedPages.has(pageNo)) return;
    try {
      const res = await corpus.getImportPageHtml(previewJobId, pageNo);
      const html = String(res.html || "").trim();
      if (!html) return;
      const staging = document.createElement("div");
      if (html.includes("<") && html.includes(">")) {
        staging.innerHTML = sanitizeHtml(html);
      } else {
        staging.innerHTML = `<pre class="import-mineru-md-draft">${escapeText(html)}</pre>`;
      }
      typesetTextbookMathIdle(staging);
      body.replaceChildren(...Array.from(staging.childNodes));
      decoratePageNumbers();
      loadedPages.add(pageNo);
    } catch (e) {
      console.warn("MinerU 页软刷新失败", pageNo, e);
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

  async function loadPagesParallel(pageNos: number[]): Promise<void> {
    const pending = pageNos.filter(
      (p) => !loadedPages.has(p) || !pageHasContent(pageBodies.get(p)),
    );
    if (!pending.length) return;
    let idx = 0;
    const workers = Array.from(
      { length: Math.min(LOAD_CONCURRENCY, pending.length) },
      async () => {
        while (idx < pending.length) {
          const pageNo = pending[idx++];
          await loadReadyPage(pageNo);
        }
      },
    );
    await Promise.all(workers);
  }

  async function appendReadyPages(job: ImportJob): Promise<void> {
    const meta = job.meta || {};
    const total = Number(
      meta.total_pages || meta.ocr_total || meta.page_count || job.page_count || 0,
    );
    if (total > 0) previewTotal = total;
    setPendingHint(job);

    const ready = readyPagesFromJob(job);
    // Only mount pages that actually have drafts — never pre-create 1..N empties.
    await loadPagesParallel(ready);

    // On terminal, fill any gaps (IR may exist without pages_ready entry).
    if (TERMINAL.has(String(job.status || "")) && previewTotal > 0) {
      const missing: number[] = [];
      for (let p = 1; p <= previewTotal; p++) {
        if (!loadedPages.has(p) || !pageHasContent(pageBodies.get(p))) missing.push(p);
      }
      if (missing.length) {
        terminalCatchUpPasses += 1;
        await loadPagesParallel(missing);
      }
    }

    if (pendingScrollPage > 0) scrollToPage(pendingScrollPage);
    decoratePageNumbers();
    bindPageNavObserver();
    if (!activePageNo && mountedPages.size > 0) {
      const first = Math.min(...mountedPages);
      if (first > 0) {
        activePageNo = first;
        emitPageNavState();
      }
    } else {
      emitPageNavState();
    }
  }

  function allPagesLoaded(job: ImportJob): boolean {
    const total =
      previewTotal ||
      Number(
        (job.meta || {}).total_pages ||
          (job.meta || {}).ocr_total ||
          job.page_count ||
          0,
      );
    if (total <= 0) return loadedPages.size > 0;
    for (let p = 1; p <= total; p++) {
      if (!loadedPages.has(p) || !pageHasContent(pageBodies.get(p))) return false;
    }
    return true;
  }

  async function onPreviewJobUpdate(job: ImportJob): Promise<void> {
    if (!previewJobId || job.id !== previewJobId) return;
    const tb = String((job as ImportJob & { textbook_node_id?: string }).textbook_node_id || "").trim();
    if (tb) {
      previewTextbookId = tb;
      ensureOpenTextbookBtn();
    }
    await appendReadyPages(job);
    const terminal = TERMINAL.has(String(job.status || ""));
    if (terminal && allPagesLoaded(job)) {
      stopPreviewPoll();
      setPendingHint(job);
    } else if (terminal && terminalCatchUpPasses >= MAX_TERMINAL_CATCHUP) {
      stopPreviewPoll();
      setPendingHint(job);
    } else if (terminal) {
      terminalCatchUpPasses += 1;
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
    previewJobId = jobId;
    void import("./activeMinerUJob").then(({ persistActiveMinerUJob }) => {
      persistActiveMinerUJob(jobId);
    });
    previewTextbookId = "";
    previewTotal = 0;
    mountedPages = new Set();
    loadedPages = new Set();
    pageBodies = new Map();
    loadingPages = new Set();
    terminalCatchUpPasses = 0;
    activePageNo = 0;
    pageNavObserver?.disconnect();
    pageNavObserver = null;
    previewPages.innerHTML = "";
    pendingBar.textContent = "连接任务…";
    pendingBar.classList.remove("hidden");
    syncPlaceholder();
    emitPageNavState();
    scheduleMinerUTreeRefresh(jobId);
    // Single Data Plane observer owns importJob HTTP; panel only reacts + loads HTML.
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
    pendingScrollPage = pageNo && pageNo > 0 ? pageNo : 0;

    // Same job already mounted: only scroll. Do not re-link CSS (FOUC / blank pages).
    if (previewJobId === jobId && mountedPages.size > 0) {
      if (pageNo && pageNo > 0) void scrollToPageWhenReady(pageNo);
      return;
    }

    ensureCss();
    ensureCriticalCss(previewRoot);
    startPreviewPoll(jobId);
    void restoreAiForJob(jobId);
    if (pageNo && pageNo > 0) void scrollToPageWhenReady(pageNo);
  }

  document.addEventListener("studio:mineru-preview", ((e: CustomEvent<{
    jobId: string;
    pageNo?: number;
  }>) => {
    if (e.detail?.jobId) openMinerUPreview(e.detail.jobId, e.detail.pageNo);
  }) as EventListener);

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
    previewJobId = "";
    previewTextbookId = "";
    previewTotal = 0;
    pendingScrollPage = 0;
    mountedPages = new Set();
    loadedPages = new Set();
    pageBodies = new Map();
    loadingPages = new Set();
    terminalCatchUpPasses = 0;
    activePageNo = 0;
    pageNavObserver?.disconnect();
    pageNavObserver = null;
    previewPages.innerHTML = "";
    pendingBar.textContent = "";
    pendingBar.classList.add("hidden");
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
      const out = await importPdf(fd);
      msg.textContent = `已入队：${out.id}`;
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
