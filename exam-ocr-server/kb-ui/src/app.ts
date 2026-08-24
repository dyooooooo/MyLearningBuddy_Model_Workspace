import { mountAgentConsole } from "./agentConsole";
import { mountAgentDock } from "./agentDock";
import { kb, type KbBook, type KbToc, type KbTocEntry } from "./api";
import { mountKbCompare } from "./compare";
import { mountKbImport } from "./import";
import { mountKbRail, type KbTabId } from "./rail";
import { bindSash } from "./sash";
import { startLive, onLive, readState, rememberState, hardReload } from "./live";
import { fillShadow, invalidateSurface, setShadowText, setSurfaceGeneration } from "./surfaces";
import { el, renderMarkdown } from "./util/dom";
import { bindKbPageToc } from "./util/page-toc";
import { mountKbViewport, type KbViewport } from "./viewport";

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

  sidebar.append(el("h1", "kb-title", "书库"));
  sidebar.append(el("p", "muted", "点一本书在右侧核对；判题读原书。"));
  const list = el("ul", "kb-list");
  const tocNav = el("nav", "kb-toc");
  tocNav.append(el("h2", "kb-panel-title", "目录"));
  const tocMount = el("div", "kb-toc-mount");
  tocNav.append(tocMount);
  sidebar.append(list, tocNav);

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
  let agentCtl: { destroy: () => void; setView: (v: "live" | "config") => void } | null = null;
  center.append(previewWrap, importPane, agentPane);

  function showTab(id: KbTabId): void {
    const importing = id === "import";
    const agenting = id === "agent" || id === "agent-config";
    root.classList.toggle("is-import", importing);
    root.classList.toggle("is-agent", agenting);
    sidebar.classList.toggle("hidden", importing || agenting);
    sideSash.classList.toggle("hidden", importing || agenting);
    previewWrap.classList.toggle("hidden", importing || agenting);
    importPane.classList.toggle("hidden", !importing);
    agentPane.classList.toggle("hidden", !agenting);
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
  let unbindSideToc: (() => void) | null = null;

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

  function renderList(): void {
    list.replaceChildren();
    for (const b of books) {
      const li = el("li", `kb-item${b.id === activeId ? " is-active" : ""}`);
      const name = el("button", "kb-item-open", b.label || b.filename) as HTMLButtonElement;
      name.type = "button";
      name.onclick = () => void openBook(b.id);
      const extra = b.is_default ? " · 默认" : "";
      const meta = el("span", "muted", `${statusLabel(b.status)} · ${b.page_count || "?"} 页${extra}`);
      const actions = el("div", "kb-item-actions");
      const rename = el("button", "btn btn-secondary", "改名") as HTMLButtonElement;
      rename.type = "button";
      rename.onclick = async (ev) => {
        ev.stopPropagation();
        const nextLabel = window.prompt("显示名", b.label);
        if (nextLabel == null) return;
        const nextHint = window.prompt("书名提示（不要带 .pdf）", b.textbook_hint || nextLabel);
        if (nextHint == null) return;
        await kb.patch(b.id, { label: nextLabel, textbook_hint: nextHint });
        await refreshList();
      };
      const def = el("button", "btn btn-secondary", b.is_default ? "已是默认" : "设为默认") as HTMLButtonElement;
      def.type = "button";
      def.disabled = b.is_default || b.status !== "ready";
      def.onclick = async (ev) => {
        ev.stopPropagation();
        await kb.patch(b.id, { is_default: true });
        await refreshList();
      };
      const redo = el("button", "btn btn-secondary", "重新识别") as HTMLButtonElement;
      redo.type = "button";
      redo.onclick = async (ev) => {
        ev.stopPropagation();
        if (!window.confirm(`重新识别「${b.label}」？`)) return;
        await kb.reimport(b.id);
        activeId = b.id;
        await refreshList();
        startPoll();
      };
      const del = el("button", "btn btn-secondary", "删除") as HTMLButtonElement;
      del.type = "button";
      del.onclick = async (ev) => {
        ev.stopPropagation();
        if (!window.confirm(`删除「${b.label}」？`)) return;
        await kb.remove(b.id);
        if (activeId === b.id) resetPreview();
        await refreshList();
      };
      actions.append(rename, def, redo, del);
      li.append(name, meta, actions);
      list.appendChild(li);
    }
  }

  function resetPreview(): void {
    activeId = "";
    lastToc = null;
    unbindPageToc?.();
    unbindPageToc = null;
    unbindSideToc?.();
    unbindSideToc = null;
    viewport?.destroy();
    viewport = null;
    compare.bindBook(null);
    previewPages.replaceChildren();
    setShadowText(tocMount, "");
    setPlaceholder(true);
    placeholder.textContent = "从左侧打开一本书";
  }

  function resolvePdfPage(entry: KbTocEntry, toc: KbToc): number {
    const mapped = entry.print_page != null ? Number(toc.print_to_pdf[String(entry.print_page)] || 0) : 0;
    return Number(entry.pdf_page || mapped || 0);
  }

  function fallbackTocHtml(toc: KbToc): string {
    const entries = toc.entries || [];
    if (!entries.length) return '<p class="muted">尚无目录（识别完成后出现）</p>';
    const items = entries.map((entry) => {
      const pdfPage = resolvePdfPage(entry, toc);
      const level = entry.is_chapter ? 0 : Number(entry.level || 1);
      const classes = [
        "kb-toc-row",
        `kb-toc-row--lv${level}`,
        entry.is_chapter ? "kb-toc-row--chapter" : "",
        pdfPage > 0 ? "kb-toc-row--link" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const attrs: string[] = [];
      if (entry.print_page != null) attrs.push(`data-goto-print-page="${entry.print_page}"`);
      if (pdfPage > 0) attrs.push(`data-goto-page="${pdfPage}"`, 'role="link"', 'tabindex="0"');
      const extra =
        entry.print_page != null
          ? `<span class="kb-toc-leader" aria-hidden="true"></span><span class="kb-toc-page">${entry.print_page}</span>`
          : "";
      const label = (entry.label || "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
      return `<li class="${classes}"${attrs.length ? ` ${attrs.join(" ")}` : ""}><span class="kb-toc-title">${label}</span>${extra}</li>`;
    });
    return `<ol class="kb-toc-list">${items.join("")}</ol>`;
  }

  async function renderToc(toc: KbToc): Promise<void> {
    lastToc = toc;
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
    await fillShadow(tocMount, "toc-tree", html || fallbackTocHtml(toc));
  }

  async function refreshList(): Promise<void> {
    books = await kb.list();
    renderList();
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
    renderList();
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
    try {
      await renderToc(await kb.toc(id));
    } catch {
      await renderToc({ book_id: id, entries: [], print_to_pdf: {}, toc_pages: [] });
    }
    for (let p = 1; p <= book.page_count; p++) ensureShell(p);
    unbindPageToc?.();
    unbindSideToc?.();
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
      getPrintToPdf: () => lastToc?.print_to_pdf || {},
    };
    unbindPageToc = bindKbPageToc(previewPages, tocBind);
    unbindSideToc = bindKbPageToc(tocMount, tocBind);
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
    if (ev.type === "surfaces") {
      const slots = Array.isArray(ev.slots) ? ev.slots.map(String) : [];
      if (slots.length) slots.forEach((s) => invalidateSurface(s));
      else invalidateSurface();
      void (async () => {
        try {
          if (lastToc) await renderToc(lastToc);
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
    if (ev.type === "reload" || (ev.type === "checkpoint" && (ev.action === "restore" || ev.action === "fork"))) {
      hardReload();
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
