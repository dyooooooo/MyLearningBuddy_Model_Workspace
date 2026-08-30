import { mountAgentConsole } from "./agentConsole";
import { mountAgentDock } from "./agentDock";
import { kb, type KbBook, type KbToc } from "./api";
import { mountKbCompare } from "./compare";
import { mountKbImport } from "./import";
import { mountKbRail, type KbTabId } from "./rail";
import { mountKbTasks } from "./tasks";
import { bindSash } from "./sash";
import { startLive, onLive, readState, rememberState, hardReload } from "./live";
import { fillShadow, invalidateSurface, setShadowText, setSurfaceGeneration } from "./surfaces";
import {
  BOOK_SORTS,
  bookSortLabel,
  parseBookSort,
  sortBooks,
  type BookSortId,
} from "./bookSort";
import { el, renderMarkdown } from "./util/dom";
import { chevron, closePopover, openPopover } from "./popover";
import { applyTocExpanded, bindKbPageToc } from "./util/page-toc";
import { renderTocHtml } from "./util/toc-html";
import { mountKbViewport, type KbViewport } from "./viewport";

type KbGroupId = "textbook" | "syllabus" | "knowledge" | "material" | "homework";

const GROUPS: { id: KbGroupId; label: string; icon: string }[] = [
  { id: "textbook", label: "教材", icon: "📕" },
  { id: "knowledge", label: "知识点", icon: "🧩" },
  { id: "syllabus", label: "考纲", icon: "📋" },
  { id: "material", label: "资料", icon: "📎" },
  { id: "homework", label: "作业", icon: "📝" },
];

const STAGES: { id: string; label: string }[] = [
  { id: "ms", label: "初中" },
  { id: "hs", label: "高中" },
];

const SUBJECTS: { id: string; label: string }[] = [
  { id: "math", label: "数学" },
  { id: "chinese", label: "语文" },
  { id: "physics", label: "物理" },
  { id: "chemistry", label: "化学" },
  { id: "biology", label: "生物" },
  { id: "english", label: "英语" },
];

const STAGE_ALIAS: Record<string, string> = {
  ms: "ms",
  junior: "ms",
  初中: "ms",
  hs: "hs",
  senior: "hs",
  高中: "hs",
  ps: "ps",
  小学: "ps",
};

const SUBJECT_ALIAS: Record<string, string> = {
  math: "math",
  mathematics: "math",
  数学: "math",
  chinese: "chinese",
  yuwen: "chinese",
  语文: "chinese",
  physics: "physics",
  物理: "physics",
  chemistry: "chemistry",
  化学: "chemistry",
  biology: "biology",
  生物: "biology",
  english: "english",
  英语: "english",
};

const CATEGORY_ALIAS: Record<string, KbGroupId> = {
  textbook: "textbook",
  教材: "textbook",
  knowledge: "knowledge",
  知识点: "knowledge",
  syllabus: "syllabus",
  考纲: "syllabus",
  material: "material",
  资料: "material",
  homework: "homework",
  作业: "homework",
};

const CATEGORY_LABEL: Record<KbGroupId, string> = {
  textbook: "教材",
  knowledge: "知识点",
  syllabus: "考纲",
  material: "资料",
  homework: "作业",
};

function groupOf(book: KbBook): KbGroupId {
  const raw = (book.category || "").trim();
  return CATEGORY_ALIAS[raw.toLowerCase()] || CATEGORY_ALIAS[raw] || "textbook";
}

function stageOf(book: KbBook): string {
  const raw = (book.grade_band || "").trim();
  return STAGE_ALIAS[raw.toLowerCase()] || STAGE_ALIAS[raw] || (raw ? "other" : "hs");
}

function subjectOf(book: KbBook): string {
  const raw = (book.subject || "").trim();
  return SUBJECT_ALIAS[raw.toLowerCase()] || SUBJECT_ALIAS[raw] || (raw || "math");
}

function caretEl(): HTMLElement {
  const n = el("span", "kb-caret");
  n.innerHTML = chevron("down");
  return n;
}

function moreDots(size = 16): string {
  return `<svg class="kb-more-ico" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>`;
}

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

  const previewWrap = el("div", "kb-preview-wrap");
  const placeholder = el("p", "muted kb-preview-placeholder", "从左侧打开一本书");
  const previewPages = el("div", "kb-preview-pages");
  previewWrap.append(placeholder, previewPages);

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
  center.append(previewWrap, importPane, agentPane, tasksPane);

  function showTab(id: KbTabId): void {
    const importing = id === "import";
    const agenting = id === "agent" || id === "agent-config";
    const tasking = id === "tasks";
    root.classList.toggle("is-import", importing);
    root.classList.toggle("is-agent", agenting);
    root.classList.toggle("is-tasks", tasking);
    sidebar.classList.toggle("hidden", importing || agenting || tasking);
    sideSash.classList.toggle("hidden", importing || agenting || tasking);
    previewWrap.classList.toggle("hidden", importing || agenting || tasking);
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
  let pollTimer: number | null = null;
  let viewport: KbViewport | null = null;
  let lastToc: KbToc | null = null;
  let unbindPageToc: (() => void) | null = null;
  const expandedBooks = new Set<string>(saved.kbExpandedBooks || []);
  const expandedGroups = new Set<string>(saved.kbExpandedGroups || []);
  const expandedToc = new Map<string, Set<string>>(
    Object.entries(saved.kbExpandedToc || {}).map(([id, rows]) => [id, new Set(rows)]),
  );
  let bookSort: BookSortId = parseBookSort(saved.kbBookSort);
  const tocCache = new Map<string, KbToc>();
  const tocMounts = new Map<string, { host: HTMLElement; unbind: () => void }>();

  const compare = mountKbCompare({
    previewWrap,
    previewPages,
    imageSrc: (bookId, pageNo) => kb.pageImageSrc(bookId, pageNo),
    getActivePage: () => viewport?.getActivePage() || 0,
    onRefreshPage: async (pageNo) => {
      await fillPage(pageNo, true);
    },
  });

  function setPlaceholder(on: boolean): void {
    placeholder.classList.toggle("hidden", !on);
  }

  function statusLabel(s: string): string {
    if (s === "ready") return "已入库";
    if (s === "converting" || s === "uploaded") return "识别中";
    if (s === "failed") return "失败";
    return s;
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

  function paintNodeExpanded(node: HTMLElement, on: boolean): void {
    node.classList.toggle("is-expanded", on);
    const tocHost = node.querySelector<HTMLElement>(".kb-node-toc");
    const caretBtn = node.querySelector<HTMLButtonElement>(".kb-caret-btn");
    if (caretBtn) {
      caretBtn.setAttribute("aria-expanded", on ? "true" : "false");
      caretBtn.setAttribute("aria-label", on ? "收起目录" : "展开目录");
    }
    tocHost?.classList.toggle("hidden", !on);
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

  function renderNode(b: KbBook): HTMLElement {
    const expanded = expandedBooks.has(b.id);
    const li = el("li", `kb-node${b.id === activeId ? " is-active" : ""}${expanded ? " is-expanded" : ""}`);
    li.dataset.bookId = b.id;
    const head = el("div", "kb-node-head");
    const caret = el("button", "kb-caret-btn") as HTMLButtonElement;
    caret.type = "button";
    caret.append(caretEl());
    caret.setAttribute("aria-label", expanded ? "收起目录" : "展开目录");
    caret.setAttribute("aria-expanded", expanded ? "true" : "false");
    caret.onclick = (ev) => {
      ev.stopPropagation();
      void toggleBook(b.id, !expandedBooks.has(b.id));
    };
    const name = el("button", "kb-item-open", b.label || b.filename) as HTMLButtonElement;
    name.type = "button";
    name.title = bookTooltip(b);
    name.onclick = () => void openBook(b.id);
    const more = el("button", "kb-more-btn") as HTMLButtonElement;
    more.type = "button";
    more.setAttribute("aria-label", "教材操作");
    more.setAttribute("aria-haspopup", "menu");
    more.insertAdjacentHTML("afterbegin", moreDots());
    more.onclick = (ev) => {
      ev.stopPropagation();
      if (li.classList.contains("is-menu-open")) {
        closePopover();
        return;
      }
      openBookMenu(more, b, li);
    };
    head.append(caret, name, bookMeta(b), more);
    const tocHost = el("div", "kb-node-toc");
    if (!expanded) tocHost.classList.add("hidden");
    li.append(head, tocHost);
    return li;
  }

  function bookTooltip(b: KbBook): string {
    return [b.label || b.filename, statusLabel(b.status), b.page_count ? `${b.page_count} 页` : "", b.is_default ? "默认" : ""]
      .filter(Boolean)
      .join(" · ");
  }

  function bookMeta(b: KbBook): HTMLElement {
    const meta = el("span", "kb-node-meta");
    if (b.is_default) meta.append(el("span", "kb-node-chip", "默认"));
    if (b.status !== "ready") {
      const kind = b.status === "failed" ? "is-fail" : "is-busy";
      meta.append(el("span", `kb-node-status ${kind}`, statusLabel(b.status)));
    } else if (b.page_count) {
      const pages = el("span", "kb-node-pages", String(b.page_count));
      pages.title = `${b.page_count} 页`;
      meta.append(pages);
    }
    return meta;
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
    await refreshList();
  }

  function renderFolder(
    id: string,
    label: string,
    count: number,
    fill: (body: HTMLElement) => void,
    opts: { icon?: string; kind?: string } = {},
  ): HTMLElement {
    const collapsed = !expandedGroups.has(id);
    const kind = opts.kind ? ` kb-group--${opts.kind}` : "";
    const li = el("li", `kb-group${kind}${collapsed ? " is-collapsed" : ""}`);
    li.dataset.groupId = id;
    const head = el("button", "kb-group-head") as HTMLButtonElement;
    head.type = "button";
    head.setAttribute("aria-expanded", collapsed ? "false" : "true");
    head.append(caretEl());
    if (opts.icon) head.append(el("span", "kb-group-icon", opts.icon));
    head.append(el("span", "kb-group-label", label), el("span", "kb-group-count muted", String(count)));
    head.onclick = () => {
      if (expandedGroups.has(id)) expandedGroups.delete(id);
      else expandedGroups.add(id);
      persistTreeState();
      renderTree();
      void restoreExpandedTocs();
    };
    const body = el("ul", "kb-group-body");
    if (collapsed) body.classList.add("hidden");
    fill(body);
    li.append(head, body);
    return li;
  }

  function emptyHint(): HTMLElement {
    return el("li", "kb-tree-empty muted", "暂无内容（导入后出现）");
  }

  function appendBooks(host: HTMLElement, items: KbBook[]): void {
    if (!items.length) host.append(emptyHint());
    else for (const b of sortBooks(items, bookSort)) host.appendChild(renderNode(b));
  }

  function renderKindFolders(host: HTMLElement, prefix: string, items: KbBook[]): void {
    const grouped = new Map<KbGroupId, KbBook[]>();
    for (const g of GROUPS) grouped.set(g.id, []);
    for (const b of items) {
      const gid = groupOf(b);
      (grouped.get(gid) || grouped.get("textbook")!).push(b);
    }
    for (const g of GROUPS) {
      const hit = grouped.get(g.id) || [];
      host.appendChild(
        renderFolder(`${prefix}/${g.id}`, g.label, hit.length, (body) => appendBooks(body, hit), {
          icon: g.icon,
          kind: "kind",
        }),
      );
    }
  }

  function renderSubjectFolders(host: HTMLElement, prefix: string, items: KbBook[]): void {
    const known = new Set(SUBJECTS.map((s) => s.id));
    for (const sub of SUBJECTS) {
      const hit = items.filter((b) => subjectOf(b) === sub.id);
      host.appendChild(
        renderFolder(`${prefix}/${sub.id}`, sub.label, hit.length, (body) => {
          renderKindFolders(body, `${prefix}/${sub.id}`, hit);
        }, { kind: "subject" }),
      );
    }
    const leftover = items.filter((b) => !known.has(subjectOf(b)));
    if (leftover.length) {
      host.appendChild(
        renderFolder(`${prefix}/other`, "其他", leftover.length, (body) => {
          renderKindFolders(body, `${prefix}/other`, leftover);
        }, { kind: "subject" }),
      );
    }
  }

  function renderStageFolders(host: HTMLElement, prefix: string, items: KbBook[]): void {
    const extra: { id: string; label: string }[] = [];
    if (items.some((b) => stageOf(b) === "ps")) extra.push({ id: "ps", label: "小学" });
    if (items.some((b) => stageOf(b) === "other")) extra.push({ id: "other", label: "其他学段" });
    for (const stage of [...STAGES, ...extra]) {
      const hit = items.filter((b) => stageOf(b) === stage.id);
      host.appendChild(
        renderFolder(`${prefix}/${stage.id}`, stage.label, hit.length, (body) => {
          renderSubjectFolders(body, `${prefix}/${stage.id}`, hit);
        }, { kind: "stage" }),
      );
    }
  }

  function renderTree(): void {
    closePopover();
    clearTocMounts();
    tree.replaceChildren();
    renderStageFolders(tree, "kb", books);
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
    renderTree();
    await restoreExpandedTocs();
    if (books.some((b) => b.status === "uploaded" || b.status === "converting")) {
      startPoll();
    }
  }

  async function fillPage(pageNo: number, force = false): Promise<void> {
    const shell = previewPages.querySelector<HTMLElement>(`.kb-page-block[data-page-no="${pageNo}"]`);
    if (!shell || !activeId) return;
    const body = shell.querySelector<HTMLElement>(".kb-page-body");
    if (!body) return;
    if (!force && !body.classList.contains("is-unloaded") && !body.classList.contains("is-error")) {
      return;
    }
    setShadowText(body, "加载中…");
    try {
      const out = await kb.pageMarkdown(activeId, pageNo);
      const html = renderMarkdown(out.prepared || out.markdown || "（本页无文本）");
      await fillShadow(body, "page-body", html);
      body.classList.remove("is-unloaded", "is-error");
      compare.wrapIfNeeded(pageNo);
    } catch (e) {
      setShadowText(body, `加载失败：${(e as Error).message}`);
      body.classList.add("is-unloaded", "is-error");
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

  async function openBook(id: string): Promise<void> {
    activeId = id;
    expandedBooks.add(id);
    persistTreeState();
    renderTree();
    await restoreExpandedTocs();
    compare.bindBook(id);
    viewport?.destroy();
    viewport = null;
    previewPages.replaceChildren();
    setPlaceholder(false);
    const book = await kb.get(id);
    if (book.status === "failed") {
      placeholder.textContent = `识别失败：${book.error || "未知错误"}`;
      setPlaceholder(true);
      return;
    }
    if (book.status !== "ready") {
      placeholder.textContent = "MinerU 识别中，完成后自动打开…";
      setPlaceholder(true);
      return;
    }
    placeholder.textContent = "从左侧打开一本书";
    lastToc = await ensureToc(id);
    for (let p = 1; p <= book.page_count; p++) ensureShell(p);
    unbindPageToc?.();
    viewport = mountKbViewport({
      root: previewPages,
      pageCount: book.page_count,
      ensureShell,
      loadPage: async (pageNo) => {
        await fillPage(pageNo);
      },
    });
    const tocBind = {
      scrollToPage: (n: number) => viewport?.scrollToPage(n),
      getPrintToPdf: () => lastToc?.print_to_pdf || tocCache.get(id)?.print_to_pdf || {},
    };
    unbindPageToc = bindKbPageToc(previewPages, tocBind);
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
