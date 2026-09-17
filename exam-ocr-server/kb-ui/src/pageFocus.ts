export const PAGE_FOCUS_EVENT = "kb:page-focus-chat";

export const BLOCK_SELECTOR = "h1,h2,h3,h4,h5,h6,p,li,table,blockquote,pre,figure,aside";

export type PageFocusDetail = {
  book_id: string;
  book?: string;
  page_no: number;
  block_id?: string;
  block_label?: string;
  quote?: string;
};

type PathNode = {
  classList?: { contains: (name: string) => boolean };
  getAttribute?: (name: string) => string | null;
  dataset?: { pageNo?: string };
};

export function nextStampId(tag: string, counts: Record<string, number>): string {
  const key = (tag || "").toLowerCase();
  counts[key] = (counts[key] || 0) + 1;
  return `${key}-${counts[key]}`;
}

export function blockLabelFromText(text: string, max = 40): string {
  const line = (text || "").replace(/\s+/g, " ").trim();
  if (!line) return "";
  if (line.length <= max) return line;
  return `${line.slice(0, max - 1)}…`;
}

export function stampBlocks(root: ParentNode): void {
  const counts: Record<string, number> = {};
  const nodes = root.querySelectorAll(BLOCK_SELECTOR);
  for (const node of nodes) {
    if (!(node instanceof HTMLElement)) continue;
    if (node.getAttribute("data-kb-block")) continue;
    const id = nextStampId(node.tagName, counts);
    node.setAttribute("data-kb-block", id);
    const label = blockLabelFromText(node.textContent || "");
    if (label) node.setAttribute("data-kb-block-label", label);
  }
  ensureFlashStyle(root);
}

export function resolveFocusFromPath(
  path: PathNode[],
  ctx: { bookId: string; bookLabel?: string },
  quote = "",
): PageFocusDetail | null {
  const bookId = (ctx.bookId || "").trim();
  if (!bookId) return null;
  let pageNo = 0;
  let blockId = "";
  let blockLabel = "";
  for (const node of path) {
    if (!pageNo) {
      const raw = node.dataset?.pageNo || node.getAttribute?.("data-page-no") || "";
      const n = Number(raw);
      if (n > 0) pageNo = n;
      else if (node.classList?.contains("kb-page-block")) {
        const fromDs = Number(node.dataset?.pageNo || 0);
        if (fromDs > 0) pageNo = fromDs;
      }
    }
    if (!blockId) {
      const id = (node.getAttribute?.("data-kb-block") || "").trim();
      if (id) {
        blockId = id;
        blockLabel = (node.getAttribute?.("data-kb-block-label") || "").trim();
      }
    }
  }
  if (pageNo <= 0) return null;
  const out: PageFocusDetail = {
    book_id: bookId,
    page_no: pageNo,
  };
  const book = (ctx.bookLabel || "").trim();
  if (book) out.book = book;
  if (blockId) out.block_id = blockId;
  if (blockLabel) out.block_label = blockLabel;
  const clipped = (quote || "").replace(/\s+/g, " ").trim().slice(0, 400);
  if (clipped) out.quote = clipped;
  return out;
}

export function resolvePageFocus(
  ev: Event,
  ctx: { bookId: string; bookLabel?: string },
): PageFocusDetail | null {
  const path = (typeof ev.composedPath === "function" ? ev.composedPath() : [ev.target]) as PathNode[];
  return resolveFocusFromPath(path, ctx, selectedQuote(ev));
}

export function buildFocusComposerText(d: PageFocusDetail): string {
  return [
    "【聚焦目标】",
    `book_id=${d.book_id}`,
    `book=${d.book || ""}`,
    `page_no=${d.page_no}`,
    `block_id=${d.block_id || ""}`,
    d.block_label ? `block_label=${d.block_label}` : "",
    d.quote ? `quote=${d.quote}` : "",
    "请基于该页/块提问或修改。先说明你要做什么。",
  ]
    .filter((line) => line !== "")
    .join("\n");
}

export function buildFocusChip(d: PageFocusDetail): string {
  const book = (d.book || d.book_id || "").trim();
  const bits = ["聚焦："];
  if (book) bits.push(book, " · ");
  bits.push(`第 ${d.page_no} 页`);
  if (d.block_label) bits.push(` · ${d.block_label}`);
  else if (d.block_id) bits.push(` · ${d.block_id}`);
  return bits.join("");
}

export function focusConversationTitle(d: PageFocusDetail): string {
  const book = (d.book || d.book_id || "").trim();
  if (book) return `${book} · 第 ${d.page_no} 页`;
  return `第 ${d.page_no} 页`;
}

export function flashFocusTarget(ev: Event): void {
  const path = typeof ev.composedPath === "function" ? ev.composedPath() : [ev.target];
  let block: HTMLElement | null = null;
  let page: HTMLElement | null = null;
  for (const node of path) {
    if (!(node instanceof HTMLElement)) continue;
    if (!block && node.hasAttribute("data-kb-block")) block = node;
    if (!page && node.classList.contains("kb-page-block")) page = node;
  }
  flashFocus(block || page);
}

export function flashFocus(el: HTMLElement | null | undefined): void {
  if (!el) return;
  ensureFlashStyle(el.getRootNode());
  el.classList.add("is-ai-focus");
  window.setTimeout(() => el.classList.remove("is-ai-focus"), 900);
}

function selectedQuote(_ev: Event): string {
  try {
    return (document.getSelection()?.toString() || "").replace(/\s+/g, " ").trim().slice(0, 400);
  } catch {
    return "";
  }
}

function ensureFlashStyle(node: ParentNode | Node): void {
  if (typeof document === "undefined") return;
  const root = "getRootNode" in node ? node.getRootNode() : node;
  const scope = root instanceof ShadowRoot ? root : document;
  if (scope.querySelector("style[data-kb-focus]")) return;
  const style = document.createElement("style");
  style.dataset.kbFocus = "1";
  style.textContent =
    ".is-ai-focus{outline:2px solid #d4a017;outline-offset:2px;background:rgba(212,160,23,.12);}";
  if (root instanceof ShadowRoot) root.appendChild(style);
  else document.head?.appendChild(style);
}
