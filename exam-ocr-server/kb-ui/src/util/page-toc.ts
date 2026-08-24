function rowFromEvent(ev: Event): HTMLElement | null {
  for (const node of ev.composedPath()) {
    if (!(node instanceof HTMLElement)) continue;
    if (node.matches("[data-goto-print-page], [data-goto-page]")) return node;
  }
  return null;
}

/** Click / keyboard jump — listens through shadow roots via composedPath. */
export function bindKbPageToc(
  root: HTMLElement,
  opts: {
    scrollToPage: (pageNo: number) => void | Promise<void>;
    getPrintToPdf: () => Record<string, number>;
  },
): () => void {
  const go = (row: HTMLElement) => {
    const pdf = Number(row.getAttribute("data-goto-page") || 0);
    const print = Number(row.getAttribute("data-goto-print-page") || 0);
    const mapped = print > 0 ? Number(opts.getPrintToPdf()[String(print)] || 0) : 0;
    const dest = pdf || mapped;
    if (dest > 0) void opts.scrollToPage(dest);
  };
  const onClick = (ev: MouseEvent) => {
    const row = rowFromEvent(ev);
    if (!row) return;
    ev.preventDefault();
    go(row);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const row = rowFromEvent(ev);
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
