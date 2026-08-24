// 导入预览「识别结果 | 原始页图」对照。
// 单滚动容器 + 每页一行（左识别 / 右原图），天然对齐，无双栏 scrollTop 同步、无改 minHeight 拉齐。

import { el } from "../../util/dom";

export type CompareImageLoader = (
  jobId: string,
  pageNo: number,
) => Promise<string | null>;

export type ImportCompareHandle = {
  isEnabled: () => boolean;
  setEnabled: (on: boolean) => void;
  bindJob: (jobId: string | null) => void;
  notifyShell: (pageNo: number) => void;
  destroy: () => void;
  toolbar: HTMLElement;
};

export type ImportCompareOptions = {
  previewWrap: HTMLElement;
  previewPages: HTMLElement;
  insertToolbarBefore?: HTMLElement | null;
  loadImageSrc: CompareImageLoader;
  htmlLabel?: string;
  imageLabel?: string;
  /** 当前预览页（1-based）；用于「刷新当前页」 */
  getActivePage?: () => number;
  /** 重新拉取并渲染指定页的识别 HTML */
  onRefreshPage?: (pageNo: number) => void | Promise<void>;
  /**
   * 对照开/关后回调。
   * `keepPage` = 切换前锁定的页码（wrap/unwrap 会改文档高度，勿用切换后的 active）。
   */
  onModeChange?: (enabled: boolean, keepPage: number) => void;
};

/**
 * 中栏顶端「对照原图」：开启后把每页包成一行左右并排，共用 previewPages 滚动。
 */
export function mountImportCompare(opts: ImportCompareOptions): ImportCompareHandle {
  const {
    previewWrap,
    previewPages,
    loadImageSrc,
    htmlLabel = "识别结果",
    imageLabel = "原始页图",
    getActivePage,
    onRefreshPage,
    onModeChange,
  } = opts;

  let enabled = false;
  let jobId: string | null = null;
  let destroyed = false;
  let refreshing = false;

  const imgCache = new Map<number, string>();
  const imgBodies = new Map<number, HTMLElement>();
  const loading = new Set<number>();

  const toolbar = el("div", "import-preview-head");
  const toggleBtn = el(
    "button",
    "btn btn-secondary import-compare-toggle",
    "对照原图",
  ) as HTMLButtonElement;
  toggleBtn.type = "button";
  toggleBtn.title = "左右对照：识别结果 ↔ 原始页图（同页同行，一起滚动）";
  const refreshBtn = el(
    "button",
    "btn btn-secondary import-refresh-page",
    "刷新当前页",
  ) as HTMLButtonElement;
  refreshBtn.type = "button";
  refreshBtn.title =
    "从最新中间表示（IR）重渲染本页并刷新显示（写回草稿；不必整书刷新）";
  const hint = el("span", "muted import-compare-hint", "");
  toolbar.append(toggleBtn, refreshBtn, hint);

  const legend = el("div", "import-compare-legend");
  legend.append(
    el("div", "import-compare-legend-cell", htmlLabel),
    el("div", "import-compare-legend-cell", imageLabel),
  );

  const before = opts.insertToolbarBefore || previewPages;
  previewWrap.insertBefore(toolbar, before);

  let mo: MutationObserver | null = null;

  function updateHint(): void {
    const hasJob = Boolean(jobId);
    refreshBtn.disabled = !hasJob || refreshing;
    if (!enabled) {
      if (!hasJob) hint.textContent = "请先打开导入任务";
      else if (!refreshing) hint.textContent = "可对照原图，或刷新当前页";
      toggleBtn.disabled = !hasJob;
      toggleBtn.textContent = "对照原图";
      toggleBtn.classList.remove("is-active");
      return;
    }
    if (!refreshing) hint.textContent = "同页左右并排，上下一起滚";
    toggleBtn.textContent = "退出对照";
    toggleBtn.classList.add("is-active");
    toggleBtn.disabled = false;
  }

  async function refreshCurrentPage(): Promise<void> {
    if (!jobId || refreshing) return;
    const pageNo = Math.floor(Number(getActivePage?.() || 0));
    if (pageNo <= 0) {
      hint.textContent = "请先滚到要刷新的页";
      return;
    }
    refreshing = true;
    refreshBtn.disabled = true;
    const label = refreshBtn.textContent || "刷新当前页";
    refreshBtn.textContent = `刷新第 ${pageNo} 页…`;
    hint.textContent = `正在刷新第 ${pageNo} 页…`;
    try {
      await onRefreshPage?.(pageNo);
      // 对照模式下同步重拉该页原图
      if (enabled) {
        imgCache.delete(pageNo);
        loading.delete(pageNo);
        const body = imgBodies.get(pageNo);
        if (body) await loadImageInto(pageNo, body);
      }
      hint.textContent = `已刷新第 ${pageNo} 页`;
    } catch (e) {
      hint.textContent = `刷新失败：${(e as Error).message || e}`;
    } finally {
      refreshing = false;
      refreshBtn.textContent = label;
      updateHint();
    }
  }

  function buildImageCell(pageNo: number): HTMLElement {
    const cell = el("div", "import-compare-cell import-compare-cell--image");
    cell.append(el("div", "import-compare-img-tag", `第 ${pageNo} 页 · 原图`));
    const body = el("div", "import-compare-img-body");
    body.textContent = "加载中…";
    cell.appendChild(body);
    imgBodies.set(pageNo, body);
    void loadImageInto(pageNo, body);
    return cell;
  }

  async function loadImageInto(pageNo: number, body: HTMLElement): Promise<void> {
    if (!jobId || loading.has(pageNo)) return;
    const show = (src: string) => {
      body.replaceChildren();
      const img = document.createElement("img");
      img.className = "import-compare-img";
      img.alt = `第 ${pageNo} 页原始图片`;
      img.src = src;
      body.appendChild(img);
    };
    const cached = imgCache.get(pageNo);
    if (cached) {
      show(cached);
      return;
    }
    loading.add(pageNo);
    body.textContent = "加载原图…";
    try {
      const src = await loadImageSrc(jobId, pageNo);
      if (!src) {
        body.textContent = "原图不可用（任务未落盘 debug 页图）";
        return;
      }
      imgCache.set(pageNo, src);
      if (destroyed || !enabled) return;
      show(src);
    } catch (e) {
      body.textContent = `原图加载失败：${(e as Error).message || e}`;
    } finally {
      loading.delete(pageNo);
    }
  }

  /** 把尚未包进行的页壳包成「左识别 | 右原图」一行。 */
  function wrapBlock(block: HTMLElement): void {
    if (!enabled || block.closest(".import-compare-row")) return;
    const pageNo = Number(block.dataset.pageNo || 0);
    if (pageNo <= 0) return;

    const row = el("div", "import-compare-row");
    row.dataset.comparePage = String(pageNo);
    const cellHtml = el("div", "import-compare-cell import-compare-cell--html");
    const cellImg = buildImageCell(pageNo);

    block.replaceWith(row);
    cellHtml.appendChild(block);
    row.append(cellHtml, cellImg);
  }

  function wrapAll(): void {
    for (const block of [
      ...previewPages.querySelectorAll<HTMLElement>(":scope > .import-page-block[data-page-no]"),
    ]) {
      wrapBlock(block);
    }
  }

  function unwrapAll(): void {
    const rows = [
      ...previewPages.querySelectorAll<HTMLElement>(":scope > .import-compare-row"),
    ];
    for (const row of rows) {
      const block = row.querySelector<HTMLElement>(":scope > .import-compare-cell--html > .import-page-block");
      if (block) row.replaceWith(block);
      else row.remove();
    }
    imgBodies.clear();
  }

  /** Read page from scroll if getActivePage is 0 / stale. */
  function pageFromScroll(): number {
    const blocks = [
      ...previewPages.querySelectorAll<HTMLElement>(".import-page-block[data-page-no]"),
    ];
    if (!blocks.length) return 0;
    const rootRect = previewPages.getBoundingClientRect();
    const anchorY = rootRect.top + rootRect.height * 0.28;
    let candidate = Number(blocks[0].dataset.pageNo || 0) || 0;
    for (const b of blocks) {
      const n = Number(b.dataset.pageNo || 0);
      if (!n) continue;
      const top = (b.closest(".import-compare-row") || b).getBoundingClientRect().top;
      if (top <= anchorY) candidate = n;
      else break;
    }
    return candidate;
  }

  function captureKeepPage(): number {
    const fromApi = Math.floor(Number(getActivePage?.() || 0));
    if (fromApi > 0) return fromApi;
    return pageFromScroll();
  }

  function enable(): void {
    if (enabled || !jobId) return;
    // Lock page BEFORE wrap — same scrollTop after taller rows = wrong page.
    const keepPage = captureKeepPage();
    enabled = true;
    previewWrap.classList.add("is-compare");
    previewPages.classList.add("is-compare-unified");
    if (!legend.isConnected) {
      previewWrap.insertBefore(legend, previewPages);
    }
    wrapAll();
    mo = new MutationObserver((mutations) => {
      if (!enabled) return;
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (!(node instanceof HTMLElement)) continue;
          if (node.classList.contains("import-page-block") && node.dataset.pageNo) {
            wrapBlock(node);
          }
        }
      }
    });
    mo.observe(previewPages, { childList: true });
    updateHint();
    if (!destroyed) onModeChange?.(true, keepPage);
  }

  function disable(): void {
    if (!enabled) return;
    // Lock page BEFORE unwrap — shorter pages make same scrollTop jump to the end.
    const keepPage = captureKeepPage();
    enabled = false;
    previewWrap.classList.remove("is-compare");
    previewPages.classList.remove("is-compare-unified");
    mo?.disconnect();
    mo = null;
    legend.remove();
    unwrapAll();
    updateHint();
    // destroy() also calls disable — skip restore during teardown.
    if (!destroyed) onModeChange?.(false, keepPage);
  }

  toggleBtn.onclick = () => {
    if (enabled) disable();
    else enable();
  };

  refreshBtn.onclick = () => {
    void refreshCurrentPage();
  };

  updateHint();

  return {
    isEnabled: () => enabled,
    setEnabled: (on: boolean) => {
      if (on) enable();
      else disable();
    },
    bindJob: (id: string | null) => {
      const next = (id || "").trim() || null;
      if (next !== jobId) {
        jobId = next;
        imgCache.clear();
        if (enabled) {
          if (!jobId) disable();
          else {
            // 任务切换：拆掉旧行再按新 job 重包
            unwrapAll();
            wrapAll();
          }
        }
      }
      updateHint();
    },
    notifyShell: (pageNo: number) => {
      if (!enabled || pageNo <= 0) return;
      const block =
        previewPages.querySelector<HTMLElement>(
          `:scope > .import-page-block[data-page-no="${pageNo}"]`,
        ) ||
        previewPages.querySelector<HTMLElement>(
          `.import-compare-row[data-compare-page="${pageNo}"] .import-page-block`,
        );
      if (block) wrapBlock(block);
    },
    destroy: () => {
      destroyed = true;
      disable();
      toolbar.remove();
      legend.remove();
      imgCache.clear();
    },
    toolbar,
  };
}
