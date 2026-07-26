// 35B line import panel. Fully isolated from the MinerU panel.

import {
  import35b,
  importPdf35b,
  type Import35bJob,
  type Import35bFeedbackItem,
} from "../../api/import35b";
import { store } from "../../state/store";
import { typesetTextbookMathIdle } from "../../util/math-render";
import { el } from "../../util/dom";
import type { PanelHandle } from "./types";
import {
  B35_GOTO_PAGE,
  B35_IMPORT_LINE,
  B35_JOB_DELETED,
  B35_NAVIGATE,
  B35_FEEDBACK_STATE,
  emitB35PreviewState,
  type B35PreviewPage,
} from "./B35ImportTree";
import { mountB35PageDebugDrawer } from "./B35PageDebugDrawer";
import { openB35FeedbackDialog } from "./B35FeedbackDialog";
import { mountStickyPageStrip } from "./StickyPageStrip";

const TERMINAL = new Set(["done", "failed", "canceled", "cancelled"]);

let cssLinked = false;
function ensureCss(): void {
  if (cssLinked) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = import35b.cssHref();
  document.head.appendChild(link);
  cssLinked = true;
}

export function mountB35PipelinePanel(): PanelHandle {
  ensureCss();

  const setupRoot = el("div", "import-line-setup import-line-35b-setup");
  const previewRoot = el("div", "import-line-preview import-line-35b-preview");

  const form = el("div", "import-upload-card");
  const formHead = el("div", "import-upload-head");
  formHead.append(
    el("h3", "", "35B 版式解析 · 新建导入"),
    el("p", "muted import-upload-sub", "难版式对照线：复杂分栏 / 公式密的教材优先用这条"),
  );

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "application/pdf";

  const labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.placeholder = "可选；留空则用 PDF 文件名";
  labelInput.className = "import-input";
  labelInput.title = "可选。不填时自动使用所选 PDF 的文件名（去掉 .pdf）";

  const resolveLabel = (file: File): string => {
    const typed = labelInput.value.trim();
    if (typed) return typed;
    return file.name.replace(/\.pdf$/i, "").trim() || file.name;
  };

  // 教材方案下拉：现有方案 + 「新建方案」「AI 方案」两个动作项
  const EDITION_ACTION_NEW = "__new_edition__";
  const EDITION_ACTION_AI = "__ai_edition__";
  const editionSelect = document.createElement("select");
  editionSelect.className = "import-input";
  editionSelect.title = "教材解析方案（必选）：决定分栏识别与校对规则";
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
  const optAi = document.createElement("option");
  optAi.value = EDITION_ACTION_AI;
  optAi.textContent = "✨ AI 方案（Copilot 扫描 PDF 并推荐）";
  actionGroup.append(optNew, optAi);
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

  const startBtn = el("button", "btn-primary", "开始解析 (35B)") as HTMLButtonElement;
  startBtn.type = "button";
  const msg = el("span", "import-msg", "");

  // Copilot 内嵌面板挂载点：出现在导入表单正下方（中栏内，不弹层）
  const copilotHost = el("div", "copilot-host");
  setupRoot.append(form, copilotHost);

  /** 活动 Copilot 会话存在时，把新选的 PDF 预上传（35b 隔离前缀）并作为消息送达 Agent。 */
  const notifyCopilotNewPdf = async (f: File): Promise<void> => {
    const { hasActiveCopilot, copilotSendText } = await import("../CopilotPanel");
    if (!hasActiveCopilot(copilotHost)) return;
    try {
      const { stagePdf } = await import("../../api/corpus");
      const staged = await stagePdf(f, "35b");
      copilotSendText(
        copilotHost,
        `我刚在导入面板选择了 PDF，已预上传完成：minio_key=${staged.minio_key}` +
          `（文件名 ${staged.filename}` +
          (staged.page_count ? `，共 ${staged.page_count} 页` : "") +
          `）。请基于这份文件继续执行。`,
      );
    } catch (e) {
      console.warn("PDF 预上传失败", e);
      copilotSendText(copilotHost, `我选择了新的 PDF「${f.name}」，但预上传失败，请稍后重试。`);
    }
  };
  fileInput.onchange = () => {
    const f = fileInput.files?.[0];
    if (f) void notifyCopilotNewPdf(f);
  };

  const previewWrap = el("div", "import-preview-wrap");
  const previewPlaceholder = el(
    "p",
    "import-preview-placeholder muted",
    "从左侧选择 35B 导入任务或页面",
  );
  const previewPages = el("div", "import-preview-pages");
  const pageStripHost = el("div", "studio-sticky-page-strip-host");
  previewWrap.append(previewPlaceholder, pageStripHost, previewPages);
  previewRoot.appendChild(previewWrap);

  const grid = el("div", "import-form-grid");

  const fileField = el("label", "import-field import-field--wide");
  fileField.append(el("span", "import-field-label", "PDF 文件"), fileInput);

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
    if (v === EDITION_ACTION_AI) {
      // 双线隔离：35B 线不传本线 job_id 给 MinerU 语料接口，只传标签与当前方案；
      // 已选 PDF → 先预上传（import35b/staging/ 前缀）拿真实 minio_key，
      // Agent 可 corpus.render_preview 渲染页面图逐页查看后给页级方案
      editionSelect.value = lastEditionValue;
      editionSelect.disabled = true;
      void (async () => {
        const f = fileInput.files?.[0];
        const payload: Record<string, unknown> = {
          label: f ? resolveLabel(f) : labelInput.value.trim(),
          edition_id: lastEditionValue.trim(),
          line: "35b",
        };
        if (f) {
          try {
            const { stagePdf } = await import("../../api/corpus");
            const staged = await stagePdf(f, "35b");
            payload.minio_key = staged.minio_key;
            payload.filename = staged.filename;
            if (staged.page_count) payload.page_count = staged.page_count;
          } catch (e) {
            // 预上传失败不阻塞会话：Agent 会按「无 minio_key」路径处理
            console.warn("PDF 预上传失败", e);
          }
        }
        const { openCopilotPanel } = await import("../CopilotPanel");
        openCopilotPanel({ kind: "import_advice", host: copilotHost, payload });
      })().finally(() => {
        editionSelect.disabled = false;
      });
      return;
    }
    lastEditionValue = v;
  };

  const actions = el("div", "import-field import-field--actions");
  actions.append(startBtn, msg);

  grid.append(fileField, labelField, pageField, editionWrap, actions);
  form.append(formHead, grid);

  let previewJobId = "";
  let previewLabel = "";
  let renderedPages = new Set<number>();
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  const pageBodies = new Map<number, HTMLElement>();
  let feedbackItems: Import35bFeedbackItem[] = [];
  let activePageNo = 0;
  let pageNavObserver: IntersectionObserver | null = null;

  function syncPlaceholder(): void {
    previewPlaceholder.classList.toggle("hidden", !!previewJobId);
  }

  syncPlaceholder();

  function pageNavPayload(): B35PreviewPage[] {
    return [...renderedPages]
      .sort((a, b) => a - b)
      .map((pageNo) => ({
        pageNo,
        feedbackCount: feedbackItems.filter((i) => i.page_no === pageNo && i.status === "open")
          .length,
      }));
  }

  let pageStrip: ReturnType<typeof mountStickyPageStrip> | null = null;

  function syncPageStrip(): void {
    if (!pageStrip) return;
    if (!previewJobId) {
      pageStrip.setPages([]);
      return;
    }
    pageStrip.setPages([...renderedPages].filter((n) => n > 0).sort((a, b) => a - b));
    if (activePageNo > 0) pageStrip.setActive(activePageNo);
  }

  function emitPageNavState(): void {
    if (!previewJobId) {
      emitB35PreviewState({ active: false });
      syncPageStrip();
      return;
    }
    emitB35PreviewState({
      active: true,
      jobId: previewJobId,
      label: previewLabel || previewJobId,
      pages: pageNavPayload(),
      activePageNo: activePageNo || undefined,
    });
    syncPageStrip();
  }

  function scrollToPage(pageNo: number): boolean {
    const block = previewPages.querySelector<HTMLElement>(`[data-page-no="${pageNo}"]`);
    if (!block) return false;
    activePageNo = pageNo;
    emitPageNavState();
    const top =
      block.getBoundingClientRect().top -
      previewPages.getBoundingClientRect().top +
      previewPages.scrollTop;
    previewPages.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
    return true;
  }

  async function scrollToPageWhenReady(pageNo: number): Promise<void> {
    for (let i = 0; i < 40; i++) {
      if (scrollToPage(pageNo)) return;
      if (previewJobId && i > 0 && i % 8 === 0) {
        try {
          const job = await import35b.job(previewJobId);
          await appendNewPages(job);
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

  async function navigateTo(detail: {
    jobId: string;
    label?: string;
    pageNo?: number;
  }): Promise<void> {
    const { jobId, label, pageNo } = detail;
    if (!jobId) return;
    if (store.importCenterView !== "preview") store.showImportPreview();
    document.dispatchEvent(new CustomEvent(B35_IMPORT_LINE));

    if (previewJobId === jobId) {
      if (pageNo && pageNo > 0) {
        if (renderedPages.has(pageNo)) {
          scrollToPage(pageNo);
        } else {
          await scrollToPageWhenReady(pageNo);
        }
      } else {
        emitPageNavState();
      }
      return;
    }

    enterLive(jobId, label || jobId);
    startPolling(jobId);
    try {
      const job = await import35b.job(jobId);
      await appendNewPages(job);
      await refreshFeedback();
    } catch {
      /* pages may stream in */
    }

    if (pageNo && pageNo > 0) {
      await scrollToPageWhenReady(pageNo);
    } else {
      emitPageNavState();
    }
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

  document.addEventListener(B35_NAVIGATE, ((e: CustomEvent<{
    jobId: string;
    label?: string;
    pageNo?: number;
  }>) => {
    void navigateTo(e.detail || { jobId: "" });
  }) as EventListener);

  document.addEventListener(B35_GOTO_PAGE, ((e: CustomEvent<{ jobId?: string; pageNo: number }>) => {
    const { jobId, pageNo } = e.detail || {};
    if (!pageNo || pageNo < 1) return;
    if (jobId && previewJobId && jobId !== previewJobId) {
      void navigateTo({ jobId, pageNo });
      return;
    }
    if (!previewJobId && jobId) {
      void navigateTo({ jobId, pageNo });
      return;
    }
    if (!previewJobId) return;
    if (renderedPages.has(pageNo)) {
      scrollToPage(pageNo);
    } else {
      void scrollToPageWhenReady(pageNo);
    }
  }) as EventListener);

  document.addEventListener(B35_JOB_DELETED, ((e: CustomEvent<{ jobId: string }>) => {
    if (e.detail?.jobId !== previewJobId) return;
    clearPreview();
  }) as EventListener);

  function clearPreview(): void {
    previewJobId = "";
    previewLabel = "";
    renderedPages = new Set();
    pageBodies.clear();
    feedbackItems = [];
    activePageNo = 0;
    pageNavObserver?.disconnect();
    pageNavObserver = null;
    previewPages.innerHTML = "";
    stopPolling();
    emitB35PreviewState({ active: false });
    syncPlaceholder();
  }

  document.addEventListener("studio:import-hub", () => {
    clearPreview();
  });

  async function refreshFeedback(): Promise<void> {
    if (!previewJobId) return;
    try {
      const data = await import35b.listFeedback(previewJobId);
      feedbackItems = data.items || [];
      document.dispatchEvent(
        new CustomEvent(B35_FEEDBACK_STATE, {
          detail: { jobId: previewJobId, items: feedbackItems },
        }),
      );
      emitPageNavState();
    } catch {
      /* transient */
    }
  }

  function enterLive(jobId: string, label: string): void {
    previewJobId = jobId;
    previewLabel = label || jobId;
    renderedPages = new Set();
    pageBodies.clear();
    feedbackItems = [];
    activePageNo = 0;
    pageNavObserver?.disconnect();
    pageNavObserver = null;
    previewPages.innerHTML = "";
    syncPlaceholder();
    emitPageNavState();
  }

  async function appendNewPages(job: Import35bJob): Promise<void> {
    if (!previewJobId) return;
    let info;
    try {
      info = await import35b.pages(previewJobId);
    } catch {
      return;
    }

    const sorted = [...info.pages].sort((a, b) => a.page_no - b.page_no);
    for (const p of sorted) {
      if (renderedPages.has(p.page_no)) continue;
      renderedPages.add(p.page_no);
      const wrap = el("div", "import-page-block");
      wrap.dataset.pageNo = String(p.page_no);
      const tagRow = el("div", "import-page-tag muted");
      const title = el("span", "", `第 ${p.page_no} 页`);
      const actions = el("span", "import-page-tag-actions");
      const reportBtn = el("button", "btn-link b35-report-btn", "反馈问题") as HTMLButtonElement;
      reportBtn.type = "button";
      reportBtn.onclick = () => {
        const pageBody = pageBodies.get(p.page_no);
        if (!pageBody) return;
        openB35FeedbackDialog({
          jobId: previewJobId,
          pageNo: p.page_no,
          pageEl: pageBody,
          onSubmitted: () => void refreshFeedback(),
        });
      };
      const reprocessBtn = el("button", "btn-link b35-reprocess-btn", "重新识别") as HTMLButtonElement;
      reprocessBtn.type = "button";
      reprocessBtn.onclick = async () => {
        if (reprocessBtn.disabled) return;
        const pageBody = pageBodies.get(p.page_no);
        reprocessBtn.disabled = true;
        const prevLabel = reprocessBtn.textContent || "重新识别";
        reprocessBtn.textContent = "识别中…";
        try {
          await import35b.reprocessPage(previewJobId, p.page_no, { mode: "full" });
          if (pageBody) {
            pageBody.innerHTML = await import35b.pageHtml(previewJobId, p.page_no);
            typesetTextbookMathIdle(pageBody);
          }
          reprocessBtn.textContent = "已更新";
          window.setTimeout(() => {
            reprocessBtn.textContent = prevLabel;
            reprocessBtn.disabled = false;
          }, 1200);
        } catch (e) {
          reprocessBtn.textContent = `失败`;
          reprocessBtn.title = (e as Error).message;
          window.setTimeout(() => {
            reprocessBtn.textContent = prevLabel;
            reprocessBtn.title = "";
            reprocessBtn.disabled = false;
          }, 2000);
        }
      };
      const more = document.createElement("details");
      more.className = "import-page-more";
      const sum = document.createElement("summary");
      sum.textContent = "更多";
      more.append(sum, reprocessBtn);
      actions.append(reportBtn, more);
      tagRow.append(title, actions);
      wrap.appendChild(tagRow);
      const body = el("div", "lb-page import-preview-35b");
      wrap.appendChild(body);
      const debugHost = el("div", "b35-page-debug-host");
      wrap.appendChild(debugHost);
      previewPages.appendChild(wrap);
      pageBodies.set(p.page_no, body);
      mountB35PageDebugDrawer(debugHost, previewJobId, p.page_no);

      body.addEventListener("click", (ev) => {
        const target = ev.target as HTMLElement | null;
        const blockEl = target?.closest?.("[data-block-id]") as HTMLElement | null;
        if (!blockEl) return;
        const blockId = blockEl.getAttribute("data-block-id") || "";
        if (!blockId) return;
        if (!confirm(`重识别此块？\n${blockId}`)) return;
        void (async () => {
          try {
            await import35b.refineBlock(previewJobId, p.page_no, blockId, {});
            body.innerHTML = await import35b.pageHtml(previewJobId, p.page_no);
            typesetTextbookMathIdle(body);
          } catch (e) {
            alert(`块精修失败：${(e as Error).message}`);
          }
        })();
      });

      try {
        body.innerHTML = await import35b.pageHtml(previewJobId, p.page_no);
        typesetTextbookMathIdle(body);
      } catch (e) {
        body.textContent = `第 ${p.page_no} 页渲染失败：${(e as Error).message}`;
      }
    }
    bindPageNavObserver();
    emitPageNavState();
    if (TERMINAL.has(String(job.status || ""))) {
      await refreshFeedback();
    }
  }

  function startPolling(jobId: string): void {
    stopPolling();
    const tick = async () => {
      try {
        const job = await import35b.job(jobId);
        if (jobId === previewJobId) await appendNewPages(job);
        if (TERMINAL.has(String(job.status || ""))) stopPolling();
      } catch {
        /* transient */
      }
    };
    tick().catch(() => undefined);
    pollTimer = setInterval(() => void tick(), 2000);
  }

  function stopPolling(): void {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

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
      const label = resolveLabel(f);
      const out = await importPdf35b({
        file: f,
        label,
        pageLimit: Number(pageLimitInput.value || 0),
        profileId: eid,
      });
      msg.textContent = `已入队：${out.id}`;
      store.showImportPreview();
      enterLive(out.id, label);
      startPolling(out.id);
      document.dispatchEvent(
        new CustomEvent(B35_NAVIGATE, { detail: { jobId: out.id, label } }),
      );
    } catch (e) {
      msg.textContent = `失败：${(e as Error).message}`;
    } finally {
      startBtn.disabled = false;
    }
  };

  async function refresh(): Promise<void> {
    if (previewJobId && pollTimer) {
      try {
        const job = await import35b.job(previewJobId);
        await appendNewPages(job);
      } catch {
        /* ignore */
      }
    }
  }

  return {
    setupRoot,
    previewRoot,
    refresh: () => void refresh(),
  };
}
