import { mountAgentConsole } from "./agentConsole";
import { mountAgentDock } from "./agentDock";
import { kb, type KbBook, type KbToc } from "./api";
import { mountKbCompare } from "./compare";
import { mountKbImport } from "./import";
import { mountIrLibrary, type IrLibrary } from "./irLibrary";
import {
  CATEGORY_LABEL,
  groupOf,
  paintLibraryTree,
  paintNodeExpanded,
} from "./libraryTree";
import { mountKbRail, type KbTabId } from "./rail";
import { mountKbTasks } from "./tasks";
import { bindSash } from "./sash";
import { startLive, onLive, readState, rememberState, hardReload } from "./live";
import { fillShadow, invalidateSurface, setShadowText, setSurfaceGeneration } from "./surfaces";
import { PAGE_FOCUS_EVENT, flashFocusTarget, resolvePageFocus, stampBlocks } from "./pageFocus";
import {
  BOOK_SORTS,
  bookSortLabel,
  parseBookSort,
  type BookSortId,
} from "./bookSort";
import { fillPagePane, panePageNo, type PaneSrc } from "./pagePane";
import { landingPage } from "./tocLanding";
import { el, renderMarkdown } from "./util/dom";
import { chevron, closePopover, openPopover } from "./popover";
import { applyTocExpanded, bindKbPageToc } from "./util/page-toc";
import { renderTocHtml } from "./util/toc-html";
import { mountKbViewport, settleScroll, type KbViewport } from "./viewport";

export function mountApp(root: HTMLElement): void {
  root.className = "kb-app";
  const sidebar = el("aside", "kb-side");
  const sideSash = el("div", "kb-sash kb-side-resize");
  sideSash.title = "拖动调整宽度 · 双击恢复默认";
  const center = el("main", "kb-main");
  const saved = readState();
  const rail = mountKbRail(root, {
    initial: (saved.tab as import("./rail").KbTabId) || "books",
    onChange: (id) => {
      rememberState({ tab: id });
      showTab(id);
    },
  });
  bindSash(sideSash, {
    panel: sidebar,
    min: 200,
    max: () => Math.max(200, Math.min(480, Math.floor(root.clientWidth * 0.4))),
    defaultWidth: 300,
    storageKey: "kb.side.w",
    onWidth: (px) => root.style.setProperty("--kb-side-w", `${px}px`),
  });
  window.addEventListener("kb:tab", ((ev: CustomEvent<KbTabId>) => {
    if (ev.detail) rail.setActive(ev.detail);
  }) as EventListener);
  const dock = el("aside", "kb-right-dock");
  const dockSash = el("div", "kb-sash kb-dock-resize");
  dockSash.title = "拖动调整宽度 · 双击恢复默认";
  mountAgentDock(dock);
  root.append(sidebar, sideSash, center, dockSash, dock);
  bindSash(dockSash, {
    panel: dock,
    min: 280,
    max: () => Math.max(280, Math.min(920, Math.floor(root.clientWidth * 0.72))),
    defaultWidth: 420,
    storageKey: "kb.dock.w",
    invert: true,
    onWidth: (px) => root.style.setProperty("--kb-dock-w", `${px}px`),
  });

  const sideHead = el("div", "kb-side-head");
  const sortBtn = el("button", "kb-sort-btn") as HTMLButtonElement;
  sortBtn.type = "button";
  sideHead.append(el("h1", "kb-title", "知识库"), sortBtn);
  sidebar.append(sideHead);
  sidebar.append(el("p", "muted kb-side-hint", "点一条资源在右侧核对；判题读原书。"));
  const tree = el("ul", "kb-tree");
  sidebar.append(tree);

  // 双库切换：教材库（老线）/ 结构化库（IR 新线）
  const libSwitch = el("div", "kb-lib-switch");
  const libBtnOld = el("button", "kb-lib-btn is-active", "教材库") as HTMLButtonElement;
  const libBtnIr = el("button", "kb-lib-btn", "结构化库") as HTMLButtonElement;
  libBtnOld.type = "button";
  libBtnIr.type = "button";
  libSwitch.append(libBtnOld, libBtnIr);
  sidebar.append(libSwitch);
  const irTree = el("ul", "kb-tree kb-ir-tree hidden");
  sidebar.append(irTree);

  const previewWrap = el("div", "kb-preview-wrap");
  const placeholder = el("p", "muted kb-preview-placeholder", "从左侧打开一本书");
  const previewPages = el("div", "kb-preview-pages");
  previewWrap.append(placeholder, previewPages);

  const irPreviewWrap = el("div", "kb-preview-wrap hidden");
  const irPlaceholder = el("p", "muted kb-preview-placeholder", "从左侧打开一本书（结构化库）");
  const irPreviewPages = el("div", "kb-preview-pages");
  irPreviewWrap.append(irPlaceholder, irPreviewPages);

  const importPane = el("div");
  mountKbImport(importPane, {
    onQueued: (book) => {
      activeId = book.id;
      void refreshList();
      startPoll();
    },
  });
  const agentPane = el("div", "kb-agent-host hidden");
  const tasksPane = el("div");
  mountKbTasks(tasksPane);
  let agentCtl: { destroy: () => void; setView: (v: "live" | "config") => void } | null = null;
  center.append(previewWrap, irPreviewWrap, importPane, agentPane, tasksPane);

  function showTab(id: KbTabId): void {
    const importing = id === "import";
    const agenting = id === "agent" || id === "agent-config";
    const tasking = id === "tasks";
    root.classList.toggle("is-import", importing);
    root.classList.toggle("is-agent", agenting);
    root.classList.toggle("is-tasks", tasking);
    sidebar.classList.toggle("hidden", importing || agenting || tasking);
    sideSash.classList.toggle("hidden", importing || agenting || tasking);
    syncLibraryPanes(!(importing || agenting || tasking));
    importPane.classList.toggle("hidden", !importing);
    agentPane.classList.toggle("hidden", !agenting);
    tasksPane.classList.toggle("hidden", !tasking);
    if (id === "books") importPane.dispatchEvent(new Event("kb:leave-import"));
    if (agenting) {
      if (!agentCtl) agentCtl = mountAgentConsole(agentPane);
      agentCtl.setView(id === "agent-config" ? "config" : "live");
    }
  }

  let books: KbBook[] = [];
  let activeId = "";
  let libMode: "old" | "ir" = "old";
  const expandedBooks = new Set<string>(saved.kbExpandedBooks || []);
  const expandedGroups = new Set<string>(saved.kbExpandedGroups || []);
  const expandedToc = new Map<string, Set<string>>(
    Object.entries(saved.kbExpandedToc || {}).map(([id, rows]) => [id, new Set(rows)]),
  );
  let bookSort: BookSortId = parseBookSort(saved.kbBookSort);
  // 结构化库自己的对照工具条：默认「中间表示 | 原图」。两库各挂一份，互不影响。
  let irLibRef: IrLibrary | null = null;
  const irCompare = mountKbCompare({
    previewWrap: irPreviewWrap,
    previewPages: irPreviewPages,
    imageSrc: (bookId, pageNo) => kb.pageImageSrc(bookId, pageNo),
    getActivePage: () => irLibRef?.getActivePage() || 0,
    scrollToPage: async (pageNo) => {
      await irLibRef?.scrollToPage(pageNo);
    },
    onRefreshPage: async (pageNo) => {
      await irLibRef?.refreshPage(pageNo);
    },
    defaultMode: "ir-image",
  });
  const irLib = mountIrLibrary({
    treeHost: irTree,
    previewHost: irPreviewPages,
    placeholder: irPlaceholder,
    getBooks: () => books,
    getSort: () => bookSort,
    expandedGroups,
    persist: () => persistTreeState(),
    savedBooks: saved.kbIrExpandedBooks,
    savedToc: saved.kbIrExpandedToc,
    onMore: (anchor, book, node) => openBookMenu(anchor, book, node),
    compare: irCompare,
  });
  irLibRef = irLib;
  irLib.reset();

  // 两个库的左树/右预览可见性只在这里算，showTab 和 setLibMode 都走它，
  // 否则切 rail 页签会把另一个库的预览强制拉回来（双面板同屏）。
  function syncLibraryPanes(booksView: boolean): void {
    tree.classList.toggle("hidden", !booksView || libMode !== "old");
    irTree.classList.toggle("hidden", !booksView || libMode !== "ir");
    previewWrap.classList.toggle("hidden", !booksView || libMode !== "old");
    irPreviewWrap.classList.toggle("hidden", !booksView || libMode !== "ir");
  }

  function setLibMode(mode: "old" | "ir"): void {
    if (libMode === mode) return;
    // 切库要接着看同一本书的同一页：两库是同一批 PDF 的两种读法，
    // 不接交接的话每次切换都退回封面，对照着看就断了。
    const handoff = mode === "ir"
      ? { id: activeId, page: viewport?.getActivePage() || 0 }
      : { id: irLib.getActiveId(), page: irLib.getActivePage() };
    libMode = mode;
    libBtnOld.classList.toggle("is-active", mode === "old");
    libBtnIr.classList.toggle("is-active", mode === "ir");
    root.classList.toggle("is-irlib", mode === "ir");
    syncLibraryPanes(true);
    if (mode === "ir") {
      irLib.setBooks(books);
      if (handoff.id) void irLib.openBook(handoff.id, handoff.page);
    } else {
      renderTree();
      void restoreExpandedTocs();
      markActiveNode(activeId);
      if (handoff.id) void openBook(handoff.id, handoff.page);
    }
  }
  libBtnOld.onclick = () => setLibMode("old");
  libBtnIr.onclick = () => setLibMode("ir");
  let pollTimer: number | null = null;
  let viewport: KbViewport | null = null;
  setLibMode("ir");
  let lastToc: KbToc | null = null;
  let unbindPageToc: (() => void) | null = null;
  const tocCache = new Map<string, KbToc>();
  const tocMounts = new Map<string, { host: HTMLElement; unbind: () => void }>();

  // 教材库默认就把「旧稿 | 中间表示」摆出来：对照关着时看不到中间表示，
  // 两条线的差异就无从下手。用户手动关掉之后不再自动打开。
  const compare = mountKbCompare({
    previewWrap,
    previewPages,
    imageSrc: (bookId, pageNo) => kb.pageImageSrc(bookId, pageNo),
    getActivePage: () => viewport?.getActivePage() || 0,
    scrollToPage: async (pageNo) => {
      if (viewport) await settleScroll(viewport, pageNo);
    },
    onRefreshPage: async (pageNo) => {
      await fillPage(pageNo, true);
    },
    defaultMode: "old-ir",
    defaultEnabled: true,
  });

  previewPages.addEventListener("dblclick", (ev) => {
    if (!activeId) return;
    const book = books.find((row) => row.id === activeId);
    const detail = resolvePageFocus(ev, {
      bookId: activeId,
      bookLabel: book?.label || book?.filename || "",
    });
    if (!detail) return;
    ev.preventDefault();
    flashFocusTarget(ev);
    window.dispatchEvent(new CustomEvent(PAGE_FOCUS_EVENT, { detail }));
  });

  function setPlaceholder(on: boolean): void {
    placeholder.classList.toggle("hidden", !on);
  }

  function paintSort(): void {
    const row = BOOK_SORTS.find((item) => item.id === bookSort);
    sortBtn.replaceChildren();
    sortBtn.append(el("span", "", bookSortLabel(bookSort)));
    sortBtn.insertAdjacentHTML("beforeend", chevron());
    sortBtn.title = row ? `排序：${row.group} · ${row.label}` : "排序";
    sortBtn.setAttribute("aria-label", sortBtn.title);
    sortBtn.setAttribute("aria-haspopup", "menu");
  }

  function applyBookSort(next: BookSortId): void {
    bookSort = next;
    persistTreeState();
    paintSort();
    renderTree();
    void restoreExpandedTocs();
    irLib.setBooks(books);
  }

  sortBtn.onclick = () => {
    if (sortBtn.classList.contains("is-open")) {
      closePopover();
      return;
    }
    sortBtn.classList.add("is-open");
    openPopover(
      sortBtn,
      (close) => {
        const menu = el("div", "kb-pop-list");
        menu.setAttribute("role", "menu");
        let lastGroup = "";
        for (const row of BOOK_SORTS) {
          if (row.group !== lastGroup) {
            lastGroup = row.group;
            menu.append(el("p", "kb-pop-h", row.group));
          }
          const btn = el("button", `kb-pop-item${row.id === bookSort ? " is-on" : ""}`) as HTMLButtonElement;
          btn.type = "button";
          btn.setAttribute("role", "menuitem");
          btn.append(el("strong", "", row.label));
          btn.onclick = () => {
            close();
            applyBookSort(row.id);
          };
          menu.append(btn);
        }
        return menu;
      },
      { width: 176, align: "right", onClose: () => sortBtn.classList.remove("is-open") },
    );
  };
  paintSort();

  function persistTreeState(): void {
    rememberState({
      kbBookSort: bookSort,
      kbExpandedBooks: [...expandedBooks],
      kbExpandedGroups: [...expandedGroups],
      kbExpandedToc: Object.fromEntries(
        [...expandedToc]
          .filter(([, ids]) => ids.size)
          .map(([id, ids]) => [id, [...ids]]),
      ),
      kbIrExpandedBooks: [...irLib.expandedBooks],
      kbIrExpandedToc: Object.fromEntries(
        [...irLib.expandedToc]
          .filter(([, ids]) => ids.size)
          .map(([id, ids]) => [id, [...ids]]),
      ),
    });
  }

  function emptyToc(id: string): KbToc {
    return { book_id: id, entries: [], print_to_pdf: {}, toc_pages: [] };
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

  async function ensureToc(id: string): Promise<KbToc> {
    const hit = tocCache.get(id);
    if (hit) return hit;
    try {
      const toc = await kb.toc(id);
      tocCache.set(id, toc);
      return toc;
    } catch {
      const empty = emptyToc(id);
      tocCache.set(id, empty);
      return empty;
    }
  }

  async function paintToc(host: HTMLElement, id: string): Promise<void> {
    const toc = await ensureToc(id);
    if (id === activeId) lastToc = toc;
    let html = "";
    try {
      const painted = await kb.surfacePaint("toc-tree", {
        entries: toc.entries || [],
        printToPdf: toc.print_to_pdf || {},
      });
      html = painted.html || "";
    } catch {
      html = "";
    }
    await fillShadow(host, "toc-tree", html || fallbackTocHtml(toc));
    applyTocExpanded(host, expandedToc.get(id) || new Set());
  }

  async function attachToc(id: string, host: HTMLElement): Promise<void> {
    detachToc(id);
    await paintToc(host, id);
    const unbind = bindKbPageToc(host, {
      scrollToPage: async (n) => {
        if (activeId !== id) await openBook(id);
        viewport?.scrollToPage(n);
      },
      getPrintToPdf: () => tocCache.get(id)?.print_to_pdf || {},
      expandedIds: expandedToc.get(id) || new Set(),
      onToggleBranch: (tocId, collapsed) => {
        let ids = expandedToc.get(id);
        if (!ids) {
          ids = new Set();
          expandedToc.set(id, ids);
        }
        if (collapsed) ids.delete(tocId);
        else ids.add(tocId);
        persistTreeState();
      },
    });
    tocMounts.set(id, { host, unbind });
  }

  async function toggleBook(id: string, on: boolean): Promise<void> {
    if (on) expandedBooks.add(id);
    else expandedBooks.delete(id);
    persistTreeState();
    const node = tree.querySelector<HTMLElement>(`.kb-node[data-book-id="${CSS.escape(id)}"]`);
    if (!node) return;
    paintNodeExpanded(node, on);
    const tocHost = node.querySelector<HTMLElement>(".kb-node-toc");
    if (!tocHost) return;
    if (on) await attachToc(id, tocHost);
    else detachToc(id);
  }

  async function restoreExpandedTocs(): Promise<void> {
    const jobs: Promise<void>[] = [];
    for (const id of expandedBooks) {
      const host = tree.querySelector<HTMLElement>(`.kb-node[data-book-id="${CSS.escape(id)}"] .kb-node-toc`);
      if (host) jobs.push(attachToc(id, host));
    }
    await Promise.all(jobs);
  }

  async function repaintAllToc(): Promise<void> {
    const jobs: Promise<void>[] = [];
    for (const [id, row] of tocMounts) {
      jobs.push(paintToc(row.host, id));
    }
    await Promise.all(jobs);
    if (activeId) lastToc = tocCache.get(activeId) || lastToc;
  }

  function openBookMenu(anchor: HTMLButtonElement, b: KbBook, node: HTMLElement): void {
    node.classList.add("is-menu-open");
    openPopover(
      anchor,
      (close) => {
        const menu = el("div", "kb-pop-list");
        menu.setAttribute("role", "menu");
        const item = (
          label: string,
          opts: { disabled?: boolean; danger?: boolean; run: () => void | Promise<void> },
        ) => {
          const btn = el("button", `kb-pop-item${opts.danger ? " is-danger" : ""}`) as HTMLButtonElement;
          btn.type = "button";
          btn.disabled = !!opts.disabled;
          btn.setAttribute("role", "menuitem");
          btn.append(el("strong", "", label));
          btn.onclick = async () => {
            close();
            await opts.run();
          };
          menu.append(btn);
        };
        item("改名", { run: () => renameBook(b) });
        item(b.is_default ? "已是默认" : "设为默认", {
          disabled: b.is_default || b.status !== "ready",
          run: () => setDefaultBook(b),
        });
        item("重新识别", { run: () => redoBook(b) });
        item("删除", { danger: true, run: () => deleteBook(b) });
        return menu;
      },
      { width: 168, align: "right", onClose: () => node.classList.remove("is-menu-open") },
    );
  }

  async function renameBook(b: KbBook): Promise<void> {
    const nextLabel = window.prompt("显示名", b.label);
    if (nextLabel == null) return;
    const nextHint = window.prompt("书名提示（不要带 .pdf）", b.textbook_hint || nextLabel);
    if (nextHint == null) return;
    const nextKind = window.prompt(
      "类型（教材 / 知识点 / 考纲 / 资料 / 作业）",
      CATEGORY_LABEL[groupOf(b)],
    );
    if (nextKind == null) return;
    await kb.patch(b.id, {
      label: nextLabel,
      textbook_hint: nextHint,
      category: nextKind.trim() || groupOf(b),
    });
    await refreshList();
  }

  async function setDefaultBook(b: KbBook): Promise<void> {
    await kb.patch(b.id, { is_default: true });
    await refreshList();
  }

  async function redoBook(b: KbBook): Promise<void> {
    if (!window.confirm(`重新识别「${b.label}」？`)) return;
    tocCache.delete(b.id);
    irLib.invalidateToc(b.id);
    await kb.reimport(b.id);
    activeId = b.id;
    await refreshList();
    startPoll();
  }

  async function deleteBook(b: KbBook): Promise<void> {
    if (!window.confirm(`删除「${b.label}」？`)) return;
    await kb.remove(b.id);
    expandedBooks.delete(b.id);
    tocCache.delete(b.id);
    detachToc(b.id);
    persistTreeState();
    if (activeId === b.id) resetPreview();
    irLib.forgetBook(b.id);
    await refreshList();
  }

  function renderTree(): void {
    closePopover();
    clearTocMounts();
    paintLibraryTree(tree, "kb", books, {
      sort: bookSort,
      expandedGroups,
      expandedBooks,
      activeId,
      onToggleGroup: (id) => {
        if (expandedGroups.has(id)) expandedGroups.delete(id);
        else expandedGroups.add(id);
        persistTreeState();
        renderTree();
        void restoreExpandedTocs();
      },
      onToggleBook: (id, on) => void toggleBook(id, on),
      onOpenBook: (id) => void openBook(id),
      onMore: (anchor, book, node) => {
        if (node.classList.contains("is-menu-open")) {
          closePopover();
          return;
        }
        openBookMenu(anchor, book, node);
      },
    });
  }

  function markActiveNode(id: string): void {
    for (const node of tree.querySelectorAll<HTMLElement>(".kb-node.is-active")) {
      node.classList.remove("is-active");
    }
    tree.querySelector<HTMLElement>(`.kb-node[data-book-id="${CSS.escape(id)}"]`)?.classList.add("is-active");
  }

  function resetPreview(): void {
    activeId = "";
    lastToc = null;
    unbindPageToc?.();
    unbindPageToc = null;
    viewport?.destroy();
    viewport = null;
    compare.bindBook(null);
    previewPages.replaceChildren();
    setPlaceholder(true);
    placeholder.textContent = "从左侧打开一本书";
  }

  function fallbackTocHtml(toc: KbToc): string {
    return renderTocHtml(toc.entries || [], toc.print_to_pdf || {});
  }

  async function refreshList(): Promise<void> {
    books = await kb.list();
    irLib.setBooks(books);
    renderTree();
    await restoreExpandedTocs();
    if (books.some((b) => b.status === "uploaded" || b.status === "converting")) {
      startPoll();
    }
  }

  async function fillMarkdownPane(host: HTMLElement, src: PaneSrc): Promise<void> {
    if (!activeId) return;
    await fillPagePane(host, activeId, panePageNo(host), src);
  }

  async function fillPage(pageNo: number, force = false): Promise<void> {
    const shell = previewPages.querySelector<HTMLElement>(`.kb-page-block[data-page-no="${pageNo}"]`);
    if (!shell || !activeId) return;
    const body = shell.querySelector<HTMLElement>(".kb-page-body");
    if (!body) return;
    if (!force && !body.classList.contains("is-unloaded") && !body.classList.contains("is-error")) {
      return;
    }
    const mode = compare.isEnabled() ? compare.getMode() : { left: "old" as const, right: "image" as const };
    const leftSrc = mode.left === "image" ? "old" : mode.left;
    await fillMarkdownPane(body, leftSrc);
    compare.wrapIfNeeded(pageNo);
    if (compare.isEnabled() && mode.right !== "image") {
      const right = compare.rightHost(pageNo);
      if (right) await fillMarkdownPane(right, mode.right);
    }
  }

  function ensureShell(pageNo: number): HTMLElement {
    let wrap = previewPages.querySelector<HTMLElement>(`.kb-page-block[data-page-no="${pageNo}"]`);
    if (wrap) return wrap;
    wrap = el("article", "kb-page-block");
    wrap.dataset.pageNo = String(pageNo);
    wrap.append(el("div", "kb-page-tag", `第 ${pageNo} 页`), el("div", "kb-page-body is-unloaded"));
    const next = [...previewPages.querySelectorAll<HTMLElement>(".kb-page-block")].find(
      (el) => Number(el.dataset.pageNo) > pageNo,
    );
    if (next) previewPages.insertBefore(wrap, next);
    else previewPages.appendChild(wrap);
    return wrap;
  }

  async function openBook(id: string, page = 0): Promise<void> {
    activeId = id;
    persistTreeState();
    markActiveNode(id);
    const tocJob =
      expandedBooks.has(id) && tocMounts.has(id) ? Promise.resolve() : toggleBook(id, true);
    compare.bindBook(id);
    viewport?.destroy();
    viewport = null;
    previewPages.replaceChildren();
    setPlaceholder(false);
    const book = await kb.get(id);
    if (book.status === "failed") {
      placeholder.textContent = `识别失败：${book.error || "未知错误"}`;
      setPlaceholder(true);
      void tocJob;
      return;
    }
    if (book.status !== "ready") {
      placeholder.textContent = "MinerU 识别中，完成后自动打开…";
      setPlaceholder(true);
      void tocJob;
      return;
    }
    placeholder.textContent = "从左侧打开一本书";
    const tocReady = ensureToc(id).then((toc) => {
      lastToc = toc;
    });
    for (let p = 1; p <= book.page_count; p++) ensureShell(p);
    // 先整本套进对照格子再滚：逐页 wrap 会把页壳换成新节点、页高跟着变，
    // 滚到章节页之后又被下面几页的重排顶回顶部。
    compare.wrapAllIfEnabled();
    unbindPageToc?.();
    const vp = mountKbViewport({
      root: previewPages,
      pageCount: book.page_count,
      ensureShell,
      loadPage: async (pageNo) => {
        await fillPage(pageNo);
      },
    });
    viewport = vp;
    const tocBind = {
      scrollToPage: (n: number) => viewport?.scrollToPage(n),
      getPrintToPdf: () => lastToc?.print_to_pdf || tocCache.get(id)?.print_to_pdf || {},
    };
    unbindPageToc = bindKbPageToc(previewPages, tocBind);
    void Promise.all([tocJob, tocReady]);
    // 两库共用同一套落地规则：目录第一条有文件页的条目。切库交接时 page 已给定，直接停那页。
    await tocReady;
    const target = page > 0
      ? Math.min(page, book.page_count)
      : landingPage(lastToc?.entries || []);
    if (target > 1) await settleScroll(vp, target);
  }

  function startPoll(): void {
    if (pollTimer != null) return;
    pollTimer = window.setInterval(() => {
      void (async () => {
        await refreshList();
        const busy = books.some((b) => b.status === "uploaded" || b.status === "converting");
        if (!busy && pollTimer != null) {
          window.clearInterval(pollTimer);
          pollTimer = null;
        }
        if (libMode === "ir") {
          const id = irLib.getActiveId();
          if (!id) return;
          const cur = books.find((b) => b.id === id);
          if (cur && cur.status === "ready" && irPreviewPages.childElementCount === 0) {
            await irLib.openBook(id);
          }
          return;
        }
        if (!activeId) return;
        const cur = books.find((b) => b.id === activeId);
        if (cur && cur.status === "ready" && previewPages.childElementCount === 0) {
          await openBook(activeId);
        }
      })();
    }, 2500);
  }

  void refreshList().then(() => {
    if (books.some((b) => b.status === "uploaded" || b.status === "converting")) startPoll();
  });

  startLive();
  onLive((ev) => {
    if (ev.generation) setSurfaceGeneration(Number(ev.generation));
    if (ev.type === "page") {
      void refreshList();
      const bookId = String(ev.book_id || "");
      const n = Number(ev.page_no || 0);
      if (libMode === "ir") {
        if (bookId && bookId === irLib.getActiveId() && n > 0) {
          void irLib.refreshPage(n);
        }
        return;
      }
      if (bookId && bookId === activeId && n > 0) {
        const block = previewPages.querySelector<HTMLElement>(`.kb-page-block[data-page-no="${n}"]`);
        const body = block?.querySelector<HTMLElement>(".kb-page-body");
        if (body && !body.classList.contains("is-unloaded")) {
          void fillPage(n, true);
        }
      }
    }
    if (ev.type === "surfaces") {
      const slots = Array.isArray(ev.slots) ? ev.slots.map(String) : [];
      if (slots.length) slots.forEach((s) => invalidateSurface(s));
      else invalidateSurface();
      if (libMode === "ir") {
        const page = irLib.getActivePage();
        if (page > 0) void irLib.refreshPage(page);
        return;
      }
      void (async () => {
        try {
          await repaintAllToc();
          const pages = [...previewPages.querySelectorAll<HTMLElement>(".kb-page-block")];
          for (const block of pages) {
            const n = Number(block.dataset.pageNo || 0);
            const body = block.querySelector<HTMLElement>(".kb-page-body");
            if (n > 0 && body && !body.classList.contains("is-unloaded")) {
              await fillPage(n, true);
            }
          }
        } catch {
          hardReload();
        }
      })();
    }
    if (ev.type === "reload" || (ev.type === "checkpoint" && ev.action === "fork")) {
      hardReload();
    }
    if (ev.type === "checkpoint" && ev.action === "restore") {
      invalidateSurface();
      void (async () => {
        try {
          await repaintAllToc();
          const pages = [...previewPages.querySelectorAll<HTMLElement>(".kb-page-block")];
          for (const block of pages) {
            const n = Number(block.dataset.pageNo || 0);
            const body = block.querySelector<HTMLElement>(".kb-page-body");
            if (n > 0 && body && !body.classList.contains("is-unloaded")) {
              await fillPage(n, true);
            }
          }
        } catch {
          /* keep current pages */
        }
      })();
    }
  });
  if (saved.tab && saved.tab !== "books") {
    rail.setActive(saved.tab as import("./rail").KbTabId);
    showTab(saved.tab as import("./rail").KbTabId);
  }
  if (typeof saved.scrollY === "number") {
    window.requestAnimationFrame(() => window.scrollTo(0, saved.scrollY || 0));
  }
}
