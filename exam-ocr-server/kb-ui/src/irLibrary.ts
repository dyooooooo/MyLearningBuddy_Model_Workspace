import { kb, type IrTocEntry, type KbBook } from "./api";
import { el } from "./util/dom";

export type IrLibrary = {
  setBooks: (books: KbBook[]) => void;
  reset: () => void;
  openBook: (id: string) => Promise<void>;
};

/** 结构化库（IR 新线）：书列表 + IR 目录树 + IR 正文预览。
 *  UI 结构 copy 老库（caret/节点/预览三区），数据走 /v1/ir/*。 */
export function mountIrLibrary(opts: {
  treeHost: HTMLElement;
  previewHost: HTMLElement;
}): IrLibrary {
  let books: KbBook[] = [];
  let activeId = "";
  const tocCache = new Map<string, IrTocEntry[]>();
  const expanded = new Set<string>();

  function placeholder(text: string): HTMLElement {
    return el("p", "muted kb-preview-placeholder", text);
  }

  function renderTree(): void {
    opts.treeHost.replaceChildren();
    for (const b of books) {
      const node = el("li", `kb-node${b.id === activeId ? " is-active" : ""}${expanded.has(b.id) ? " is-expanded" : ""}`);
      node.dataset.bookId = b.id;
      const head = el("div", "kb-node-head");
      const caret = el("button", "kb-caret-btn") as HTMLButtonElement;
      caret.type = "button";
      caret.setAttribute("aria-label", expanded.has(b.id) ? "收起目录" : "展开目录");
      caret.append(expanded.has(b.id) ? "▾" : "▸");
      caret.onclick = (ev) => {
        ev.stopPropagation();
        if (expanded.has(b.id)) {
          expanded.delete(b.id);
          renderTree();
        } else {
          expanded.add(b.id);
          renderTree();
          void attachToc(b.id);
        }
      };
      const name = el("button", "kb-item-open", b.label || b.filename) as HTMLButtonElement;
      name.type = "button";
      name.onclick = () => void openBook(b.id);
      const chip = el("span", "kb-node-chip", "IR");
      head.append(caret, name, chip);
      node.append(head);
      const tocHost = el("div", "kb-node-toc");
      if (!expanded.has(b.id)) tocHost.classList.add("hidden");
      node.append(tocHost);
      opts.treeHost.append(node);
    }
  }

  async function ensureToc(id: string): Promise<IrTocEntry[]> {
    const hit = tocCache.get(id);
    if (hit) return hit;
    try {
      const toc = await kb.irToc(id);
      const entries = toc.entries || [];
      tocCache.set(id, entries);
      return entries;
    } catch {
      return [];
    }
  }

  async function attachToc(id: string): Promise<void> {
    const node = opts.treeHost.querySelector<HTMLElement>(`.kb-node[data-book-id="${CSS.escape(id)}"]`);
    const host = node?.querySelector<HTMLElement>(".kb-node-toc");
    if (!host) return;
    host.replaceChildren(el("p", "muted", "加载目录…"));
    const entries = await ensureToc(id);
    host.replaceChildren(tocList(id, entries));
  }

  function tocList(id: string, entries: IrTocEntry[]): HTMLElement {
    const root = el("ul", "kb-ir-toc");
    const stack: { level: number; ul: HTMLElement }[] = [{ level: -1, ul: root }];
    for (const entry of entries) {
      while (stack.length > 1 && entry.level <= stack[stack.length - 1].level) stack.pop();
      const parent = stack[stack.length - 1].ul;
      const li = el("li", `kb-ir-toc-item lv${entry.level}`);
      const btn = el("button", "kb-ir-toc-link", entry.title) as HTMLButtonElement;
      btn.type = "button";
      btn.disabled = !entry.pdf_page;
      btn.title = entry.pdf_page ? `第 ${entry.pdf_page} 页` : "页码未定位";
      btn.onclick = () => {
        if (entry.pdf_page) void openPage(id, entry.pdf_page!);
      };
      li.append(btn);
      parent.append(li);
      const child = el("ul", "kb-ir-toc");
      li.append(child);
      stack.push({ level: entry.level, ul: child });
    }
    // 清掉空子 ul
    for (const ul of root.querySelectorAll("ul")) {
      if (!ul.childElementCount) ul.remove();
    }
    return root;
  }

  async function openBook(id: string): Promise<void> {
    expanded.add(id);
    renderTree();
    await attachToc(id);
    const entries = await ensureToc(id);
    const first = entries.find((e) => e.pdf_page);
    if (first?.pdf_page) await openPage(id, first.pdf_page);
    else showPlaceholder(id, "这本书的目录还没有定位到页码");
  }

  function markActive(id: string): void {
    for (const node of opts.treeHost.querySelectorAll<HTMLElement>(".kb-node.is-active")) {
      node.classList.remove("is-active");
    }
    opts.treeHost
      .querySelector<HTMLElement>(`.kb-node[data-book-id="${CSS.escape(id)}"]`)
      ?.classList.add("is-active");
  }

  let openSeq = 0;

  async function openPage(id: string, pageNo: number): Promise<void> {
    activeId = id;
    const seq = ++openSeq;
    // 只更新高亮：整树 renderTree 会把 attachToc 填充的目录抹成空 div
    markActive(id);
    showPlaceholder(id, `加载第 ${pageNo} 页…`);
    try {
      const out = await kb.irPage(id, pageNo);
      if (seq !== openSeq) return; // 已被更新的请求取代，丢弃旧响应
      const block = el("div", "kb-page-block");
      block.dataset.pageNo = String(pageNo);
      const host = el("div", "kb-page-body");
      block.append(host);
      opts.previewHost.replaceChildren(block);
      const { fillShadow } = await import("./surfaces");
      await fillShadow(host, "page-body", out.prepared || out.markdown || "（本页无文本）");
    } catch {
      if (seq === openSeq) showPlaceholder(id, `第 ${pageNo} 页加载失败`);
    }
  }

  function showPlaceholder(_id: string, text: string): void {
    opts.previewHost.replaceChildren(placeholder(text));
  }

  return {
    setBooks(list) {
      books = list;
      renderTree();
    },
    reset() {
      activeId = "";
      tocCache.clear();
      renderTree();
      opts.previewHost.replaceChildren(placeholder("从左侧打开一本书"));
    },
    openBook,
  };
}
