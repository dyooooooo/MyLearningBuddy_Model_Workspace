import { kb, type IrTocEntry, type KbBook, type KbTocEntry } from "./api";
import { epochAlive } from "./epoch";
import type { KbCompare } from "./compare";
import { paintLibraryTree, paintNodeExpanded } from "./libraryTree";
import { fillPagePane } from "./pagePane";
import { fillShadow } from "./surfaces";
import { applyTocExpanded, bindKbPageToc } from "./util/page-toc";
import { renderTocHtml } from "./util/toc-html";
import { landingPage } from "./tocLanding";
import { el } from "./util/dom";
import { mountKbViewport, settleScroll, type KbViewport } from "./viewport";
import type { BookSortId } from "./bookSort";

export { epochAlive } from "./epoch";

export type IrLibrary = {
  setBooks: (books: KbBook[]) => void;
  reset: () => void;
  /** `page` 由切库交接传入：停在教材库那边看的同一页，而不是重新落地。 */
  openBook: (id: string, page?: number) => Promise<void>;
  forgetBook: (id: string) => void;
  invalidateToc: (id: string) => void;
  getActiveId: () => string;
  getActivePage: () => number;
  /** 开关对照后滚回原来那页。 */
  scrollToPage: (pageNo: number) => Promise<void>;
  /** 对照工具条的「刷新当前页」走这里：强制重画，连带右格。 */
  refreshPage: (pageNo: number) => Promise<void>;
  expandedBooks: Set<string>;
  expandedToc: Map<string, Set<string>>;
};

type IrTocPaint = {
  entries: KbTocEntry[];
  printToPdf: Record<string, number>;
};

function toKbToc(raw: IrTocEntry[]): IrTocPaint {
  const entries: KbTocEntry[] = raw.map((e) => ({
    label: e.label || e.title,
    print_page: e.print_page != null && String(e.print_page) !== "" ? Number(e.print_page) : null,
    pdf_page: e.pdf_page ?? null,
    is_chapter: e.is_chapter ?? e.level === 0,
    level: e.level,
  }));
  const printToPdf: Record<string, number> = {};
  for (const e of entries) {
    if (e.print_page != null && Number.isFinite(e.print_page) && e.pdf_page) {
      printToPdf[String(e.print_page)] = e.pdf_page;
    }
  }
  return { entries, printToPdf };
}

/** 结构化库（中间表示线）：左栏与教材库同一套文件夹/书节点，数据走 /v1/ir/*。 */
export function mountIrLibrary(opts: {
  treeHost: HTMLElement;
  previewHost: HTMLElement;
  placeholder: HTMLElement;
  getBooks: () => KbBook[];
  getSort: () => BookSortId;
  expandedGroups: Set<string>;
  persist: () => void;
  savedBooks?: string[];
  savedToc?: Record<string, string[]>;
  onMore?: (anchor: HTMLButtonElement, book: KbBook, node: HTMLElement) => void;
  /** 结构化库自己的对照工具条（默认 IR|原图），与教材库那一份各自独立。 */
  compare?: KbCompare;
}): IrLibrary {
  let activeId = "";
  let epoch = 0;
  const tocCache = new Map<string, IrTocPaint>();
  const expandedBooks = new Set<string>(opts.savedBooks || []);
  const expandedToc = new Map<string, Set<string>>(
    Object.entries(opts.savedToc || {}).map(([id, rows]) => [id, new Set(rows)]),
  );
  const tocMounts = new Map<string, { host: HTMLElement; unbind: () => void }>();
  let viewport: KbViewport | null = null;
  // 正文里 page-ir 画的印刷目录页：行带 data-goto-page，点行跳页。
  bindKbPageToc(opts.previewHost, {
    scrollToPage: async (n) => {
      if (viewport) await settleScroll(viewport, n);
    },
    getPrintToPdf: () => tocCache.get(activeId)?.printToPdf || {},
  });

  function setPlaceholder(on: boolean, text?: string): void {
    if (text) opts.placeholder.textContent = text;
    opts.placeholder.classList.toggle("hidden", !on);
  }

  function persist(): void {
    opts.persist();
  }

  function detachToc(id: string): void {
    const row = tocMounts.get(id);
    if (!row) return;
    row.unbind();
    tocMounts.delete(id);
  }

  function clearTocMounts(): void {
    for (const id of [...tocMounts.keys()]) detachToc(id);
  }

  async function ensureToc(id: string): Promise<IrTocPaint> {
    const hit = tocCache.get(id);
    if (hit) return hit;
    try {
      const toc = await kb.irToc(id);
      const painted = toKbToc(toc.entries || []);
      tocCache.set(id, painted);
      return painted;
    } catch {
      const empty = { entries: [], printToPdf: {} };
      tocCache.set(id, empty);
      return empty;
    }
  }

  async function paintToc(host: HTMLElement, id: string): Promise<void> {
    const toc = await ensureToc(id);
    let html = "";
    try {
      const painted = await kb.surfacePaint("toc-tree", {
        entries: toc.entries,
        printToPdf: toc.printToPdf,
      });
      html = painted.html || "";
    } catch {
      html = "";
    }
    await fillShadow(host, "toc-tree", html || renderTocHtml(toc.entries, toc.printToPdf));
    applyTocExpanded(host, expandedToc.get(id) || new Set());
  }

  async function attachToc(id: string, host: HTMLElement): Promise<void> {
    detachToc(id);
    await paintToc(host, id);
    const unbind = bindKbPageToc(host, {
      scrollToPage: async (n) => {
        if (activeId !== id) await openBook(id);
        await viewport?.scrollToPage(n);
      },
      getPrintToPdf: () => tocCache.get(id)?.printToPdf || {},
      expandedIds: expandedToc.get(id) || new Set(),
      onToggleBranch: (tocId, collapsed) => {
        let ids = expandedToc.get(id);
        if (!ids) {
          ids = new Set();
          expandedToc.set(id, ids);
        }
        if (collapsed) ids.delete(tocId);
        else ids.add(tocId);
        persist();
      },
    });
    tocMounts.set(id, { host, unbind });
  }

  async function restoreExpandedTocs(): Promise<void> {
    const jobs: Promise<void>[] = [];
    for (const id of expandedBooks) {
      const node = opts.treeHost.querySelector<HTMLElement>(`.kb-node[data-book-id="${CSS.escape(id)}"]`);
      const host = node?.querySelector<HTMLElement>(".kb-node-toc");
      if (host) jobs.push(attachToc(id, host));
      node?.querySelector(":scope > .kb-ir-review")?.remove();
    }
    await Promise.all(jobs);
  }

  async function toggleBook(id: string, on: boolean): Promise<void> {
    if (on) expandedBooks.add(id);
    else expandedBooks.delete(id);
    persist();
    const node = opts.treeHost.querySelector<HTMLElement>(`.kb-node[data-book-id="${CSS.escape(id)}"]`);
    if (!node) return;
    paintNodeExpanded(node, on);
    node.querySelector(":scope > .kb-ir-review")?.remove();
    const tocHost = node.querySelector<HTMLElement>(".kb-node-toc");
    if (!tocHost) return;
    if (on) await attachToc(id, tocHost);
    else detachToc(id);
  }

  function renderTree(): void {
    clearTocMounts();
    paintLibraryTree(opts.treeHost, "kb-ir", opts.getBooks(), {
      sort: opts.getSort(),
      expandedGroups: opts.expandedGroups,
      expandedBooks,
      activeId,
      onToggleGroup: (id) => {
        if (opts.expandedGroups.has(id)) opts.expandedGroups.delete(id);
        else opts.expandedGroups.add(id);
        persist();
        renderTree();
        void restoreExpandedTocs();
      },
      onToggleBook: (id, on) => void toggleBook(id, on),
      onOpenBook: (id) => void openBook(id),
      onMore: opts.onMore,
    });
  }

  function markActive(id: string): void {
    for (const node of opts.treeHost.querySelectorAll<HTMLElement>(".kb-node.is-active")) {
      node.classList.remove("is-active");
    }
    opts.treeHost
      .querySelector<HTMLElement>(`.kb-node[data-book-id="${CSS.escape(id)}"]`)
      ?.classList.add("is-active");
  }

  function ensureShell(pageNo: number): HTMLElement {
    let wrap = opts.previewHost.querySelector<HTMLElement>(`.kb-page-block[data-page-no="${pageNo}"]`);
    if (wrap) return wrap;
    wrap = el("article", "kb-page-block");
    wrap.dataset.pageNo = String(pageNo);
    wrap.append(el("div", "kb-page-tag", `第 ${pageNo} 页`), el("div", "kb-page-body is-unloaded"));
    const next = [...opts.previewHost.querySelectorAll<HTMLElement>(".kb-page-block")].find(
      (node) => Number(node.dataset.pageNo) > pageNo,
    );
    if (next) opts.previewHost.insertBefore(wrap, next);
    else opts.previewHost.appendChild(wrap);
    return wrap;
  }

  async function loadPage(pageNo: number, shell: HTMLElement, force = false): Promise<void> {
    const bookId = activeId;
    if (!bookId) return;
    const host = shell.querySelector<HTMLElement>(".kb-page-body");
    if (!host) return;
    const stale = host.classList.contains("is-unloaded") || host.classList.contains("is-error");
    if (!force && !stale) return;
    const compare = opts.compare;
    const mode = compare?.isEnabled() ? compare.getMode() : null;
    // 对照关着时结构化库只画中间表示；开着时左格按模式来（旧|IR 会在这一格读老线）。
    await fillPagePane(host, bookId, pageNo, mode && mode.left !== "image" ? mode.left : "ir");
    compare?.wrapIfNeeded(pageNo);
    if (mode && mode.right !== "image") {
      const right = compare?.rightHost(pageNo);
      if (right) await fillPagePane(right, bookId, pageNo, mode.right);
    }
  }

  async function openBook(id: string, page = 0): Promise<void> {
    const token = ++epoch;
    activeId = id;
    markActive(id);
    opts.compare?.bindBook(id);
    const tocJob = expandedBooks.has(id) && tocMounts.has(id) ? Promise.resolve() : toggleBook(id, true);
    viewport?.destroy();
    viewport = null;
    opts.previewHost.replaceChildren();
    setPlaceholder(false);
    const book = await kb.get(id);
    if (!epochAlive(epoch, token)) return;
    if (book.status === "failed") {
      setPlaceholder(true, `识别失败：${book.error || "未知错误"}`);
      void tocJob;
      return;
    }
    if (book.status !== "ready") {
      setPlaceholder(true, "MinerU 识别中，完成后自动打开…");
      void tocJob;
      return;
    }
    const tocReady = ensureToc(id);
    const count = Math.max(0, Number(book.page_count) || 0);
    if (!count) {
      setPlaceholder(true, "这本书还没有页");
      void tocJob;
      return;
    }
    for (let p = 1; p <= count; p++) ensureShell(p);
    opts.compare?.wrapAllIfEnabled();
    const view = mountKbViewport({
      root: opts.previewHost,
      pageCount: count,
      ensureShell,
      loadPage,
    });
    viewport = view;
    const painted = await tocReady;
    if (!epochAlive(epoch, token)) return;
    const target = page > 0 ? Math.min(page, count) : landingPage(painted.entries);
    if (target > 1) await settleScroll(view, target);
    if (!epochAlive(epoch, token)) return;
    void tocJob;
  }

  function resetPreview(): void {
    activeId = "";
    opts.compare?.bindBook(null);
    viewport?.destroy();
    viewport = null;
    opts.previewHost.replaceChildren();
    setPlaceholder(true, "从左侧打开一本书（结构化库）");
  }

  return {
    expandedBooks,
    expandedToc,
    setBooks() {
      renderTree();
      void restoreExpandedTocs();
    },
    reset() {
      tocCache.clear();
      resetPreview();
      renderTree();
    },
    openBook,
    getActiveId: () => activeId,
    getActivePage: () => viewport?.getActivePage() || 0,
    async scrollToPage(pageNo) {
      const view = viewport;
      if (view && pageNo > 0) await settleScroll(view, pageNo);
    },
    async refreshPage(pageNo) {
      const shell = opts.previewHost.querySelector<HTMLElement>(
        `.kb-page-block[data-page-no="${pageNo}"], .kb-compare-row[data-compare-page="${pageNo}"] .kb-page-block`,
      );
      if (shell) await loadPage(pageNo, shell, true);
    },
    forgetBook(id) {
      expandedBooks.delete(id);
      tocCache.delete(id);
      expandedToc.delete(id);
      detachToc(id);
      persist();
      if (activeId === id) resetPreview();
    },
    invalidateToc(id) {
      tocCache.delete(id);
    },
  };
}
