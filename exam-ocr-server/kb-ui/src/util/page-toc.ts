function fromPath(ev: Event, selector: string): HTMLElement | null {
  for (const node of ev.composedPath()) {
    if (!(node instanceof HTMLElement)) continue;
    if (node.matches(selector)) return node;
  }
  return null;
}

export function setTocCollapsed(node: HTMLElement, on: boolean): void {
  node.classList.toggle("is-collapsed", on);
  const caret = node.querySelector(".kb-toc-caret");
  if (caret) {
    caret.setAttribute("aria-expanded", on ? "false" : "true");
    caret.setAttribute("aria-label", on ? "展开" : "收起");
  }
}

export function applyTocExpanded(host: HTMLElement, ids: Set<string>): void {
  const root = host.shadowRoot || host;
  for (const node of root.querySelectorAll<HTMLElement>(".kb-toc-node--branch[data-toc-id]")) {
    const id = node.getAttribute("data-toc-id") || "";
    setTocCollapsed(node, !ids.has(id));
  }
}

function toggleBranch(
  caret: HTMLElement,
  onToggleBranch?: (id: string, collapsed: boolean) => void,
): void {
  const node = caret.closest(".kb-toc-node");
  if (!(node instanceof HTMLElement)) return;
  const next = !node.classList.contains("is-collapsed");
  setTocCollapsed(node, next);
  const id = node.getAttribute("data-toc-id") || "";
  if (id) onToggleBranch?.(id, next);
}

/** Click / keyboard jump — listens through shadow roots via composedPath. */
export function bindKbPageToc(
  root: HTMLElement,
  opts: {
    scrollToPage: (pageNo: number) => void | Promise<void>;
    getPrintToPdf: () => Record<string, number>;
    expandedIds?: Set<string>;
    onToggleBranch?: (id: string, collapsed: boolean) => void;
  },
): () => void {
  if (opts.expandedIds) applyTocExpanded(root, opts.expandedIds);
  const go = (row: HTMLElement) => {
    const pdf = Number(row.getAttribute("data-goto-page") || 0);
    const print = Number(row.getAttribute("data-goto-print-page") || 0);
    const mapped = print > 0 ? Number(opts.getPrintToPdf()[String(print)] || 0) : 0;
    const dest = pdf || mapped;
    if (dest > 0) void opts.scrollToPage(dest);
  };
  const onClick = (ev: MouseEvent) => {
    const caret = fromPath(ev, ".kb-toc-caret");
    if (caret) {
      ev.preventDefault();
      ev.stopPropagation();
      toggleBranch(caret, opts.onToggleBranch);
      return;
    }
    const row = fromPath(ev, "[data-goto-print-page], [data-goto-page]");
    if (!row) return;
    ev.preventDefault();
    go(row);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const caret = fromPath(ev, ".kb-toc-caret");
    if (caret) {
      ev.preventDefault();
      toggleBranch(caret, opts.onToggleBranch);
      return;
    }
    const row = fromPath(ev, "[data-goto-print-page], [data-goto-page]");
    if (!row) return;
    ev.preventDefault();
    go(row);
  };
  root.addEventListener("click", onClick);
  root.addEventListener("keydown", onKey);
  return () => {
    root.removeEventListener("click", onClick);
    root.removeEventListener("keydown", onKey);
  };
}
