import type { KbBook } from "./api";
import { sortBooks, type BookSortId } from "./bookSort";
import { el } from "./util/dom";
import { chevron } from "./popover";

export type KbGroupId = "textbook" | "syllabus" | "knowledge" | "material" | "homework";

export const GROUPS: { id: KbGroupId; label: string; icon: string }[] = [
  { id: "textbook", label: "教材", icon: "📕" },
  { id: "knowledge", label: "知识点", icon: "🧩" },
  { id: "syllabus", label: "考纲", icon: "📋" },
  { id: "material", label: "资料", icon: "📎" },
  { id: "homework", label: "作业", icon: "📝" },
];

export const STAGES: { id: string; label: string }[] = [
  { id: "ms", label: "初中" },
  { id: "hs", label: "高中" },
];

export const SUBJECTS: { id: string; label: string }[] = [
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

export const CATEGORY_LABEL: Record<KbGroupId, string> = {
  textbook: "教材",
  knowledge: "知识点",
  syllabus: "考纲",
  material: "资料",
  homework: "作业",
};

export function groupOf(book: KbBook): KbGroupId {
  const raw = (book.category || "").trim();
  return CATEGORY_ALIAS[raw.toLowerCase()] || CATEGORY_ALIAS[raw] || "textbook";
}

export function stageOf(book: KbBook): string {
  const raw = (book.grade_band || "").trim();
  return STAGE_ALIAS[raw.toLowerCase()] || STAGE_ALIAS[raw] || (raw ? "other" : "hs");
}

export function subjectOf(book: KbBook): string {
  const raw = (book.subject || "").trim();
  return SUBJECT_ALIAS[raw.toLowerCase()] || SUBJECT_ALIAS[raw] || (raw || "math");
}

export function caretEl(): HTMLElement {
  const n = el("span", "kb-caret");
  n.innerHTML = chevron("down");
  return n;
}

export function moreDots(size = 16): string {
  return `<svg class="kb-more-ico" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>`;
}

export function statusLabel(s: string): string {
  if (s === "ready") return "已入库";
  if (s === "converting" || s === "uploaded") return "识别中";
  if (s === "failed") return "失败";
  return s;
}

export function bookTooltip(b: KbBook): string {
  return [b.label || b.filename, statusLabel(b.status), b.page_count ? `${b.page_count} 页` : "", b.is_default ? "默认" : ""]
    .filter(Boolean)
    .join(" · ");
}

export function bookMeta(b: KbBook): HTMLElement {
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

export type LibraryTreeCtx = {
  sort: BookSortId;
  expandedGroups: Set<string>;
  expandedBooks: Set<string>;
  activeId: string;
  onToggleGroup: (id: string) => void;
  onToggleBook: (id: string, on: boolean) => void;
  onOpenBook: (id: string) => void;
  onMore?: (anchor: HTMLButtonElement, book: KbBook, node: HTMLElement) => void;
};

function emptyHint(): HTMLElement {
  return el("li", "kb-tree-empty muted", "暂无内容（导入后出现）");
}

function renderBookNode(b: KbBook, ctx: LibraryTreeCtx): HTMLElement {
  const expanded = ctx.expandedBooks.has(b.id);
  const li = el("li", `kb-node${b.id === ctx.activeId ? " is-active" : ""}${expanded ? " is-expanded" : ""}`);
  li.dataset.bookId = b.id;
  const head = el("div", "kb-node-head");
  const caret = el("button", "kb-caret-btn") as HTMLButtonElement;
  caret.type = "button";
  caret.append(caretEl());
  caret.setAttribute("aria-label", expanded ? "收起目录" : "展开目录");
  caret.setAttribute("aria-expanded", expanded ? "true" : "false");
  caret.onclick = (ev) => {
    ev.stopPropagation();
    ctx.onToggleBook(b.id, !ctx.expandedBooks.has(b.id));
  };
  const name = el("button", "kb-item-open", b.label || b.filename) as HTMLButtonElement;
  name.type = "button";
  name.title = bookTooltip(b);
  name.onclick = () => ctx.onOpenBook(b.id);
  head.append(caret, name, bookMeta(b));
  if (ctx.onMore) {
    const more = el("button", "kb-more-btn") as HTMLButtonElement;
    more.type = "button";
    more.setAttribute("aria-label", "教材操作");
    more.setAttribute("aria-haspopup", "menu");
    more.insertAdjacentHTML("afterbegin", moreDots());
    more.onclick = (ev) => {
      ev.stopPropagation();
      ctx.onMore?.(more, b, li);
    };
    head.append(more);
  }
  const tocHost = el("div", "kb-node-toc");
  if (!expanded) tocHost.classList.add("hidden");
  li.append(head, tocHost);
  return li;
}

function renderFolder(
  id: string,
  label: string,
  count: number,
  fill: (body: HTMLElement) => void,
  ctx: LibraryTreeCtx,
  opts: { icon?: string; kind?: string } = {},
): HTMLElement {
  const collapsed = !ctx.expandedGroups.has(id);
  const kind = opts.kind ? ` kb-group--${opts.kind}` : "";
  const li = el("li", `kb-group${kind}${collapsed ? " is-collapsed" : ""}`);
  li.dataset.groupId = id;
  const head = el("button", "kb-group-head") as HTMLButtonElement;
  head.type = "button";
  head.setAttribute("aria-expanded", collapsed ? "false" : "true");
  head.append(caretEl());
  if (opts.icon) head.append(el("span", "kb-group-icon", opts.icon));
  head.append(el("span", "kb-group-label", label), el("span", "kb-group-count muted", String(count)));
  head.onclick = () => ctx.onToggleGroup(id);
  const body = el("ul", "kb-group-body");
  if (collapsed) body.classList.add("hidden");
  fill(body);
  li.append(head, body);
  return li;
}

function appendBooks(host: HTMLElement, items: KbBook[], ctx: LibraryTreeCtx): void {
  if (!items.length) host.append(emptyHint());
  else for (const b of sortBooks(items, ctx.sort)) host.appendChild(renderBookNode(b, ctx));
}

function renderKindFolders(host: HTMLElement, prefix: string, items: KbBook[], ctx: LibraryTreeCtx): void {
  const grouped = new Map<KbGroupId, KbBook[]>();
  for (const g of GROUPS) grouped.set(g.id, []);
  for (const b of items) {
    const gid = groupOf(b);
    (grouped.get(gid) || grouped.get("textbook")!).push(b);
  }
  for (const g of GROUPS) {
    const hit = grouped.get(g.id) || [];
    host.appendChild(
      renderFolder(`${prefix}/${g.id}`, g.label, hit.length, (body) => appendBooks(body, hit, ctx), ctx, {
        icon: g.icon,
        kind: "kind",
      }),
    );
  }
}

function renderSubjectFolders(host: HTMLElement, prefix: string, items: KbBook[], ctx: LibraryTreeCtx): void {
  const known = new Set(SUBJECTS.map((s) => s.id));
  for (const sub of SUBJECTS) {
    const hit = items.filter((b) => subjectOf(b) === sub.id);
    host.appendChild(
      renderFolder(`${prefix}/${sub.id}`, sub.label, hit.length, (body) => {
        renderKindFolders(body, `${prefix}/${sub.id}`, hit, ctx);
      }, ctx, { kind: "subject" }),
    );
  }
  const leftover = items.filter((b) => !known.has(subjectOf(b)));
  if (leftover.length) {
    host.appendChild(
      renderFolder(`${prefix}/other`, "其他", leftover.length, (body) => {
        renderKindFolders(body, `${prefix}/other`, leftover, ctx);
      }, ctx, { kind: "subject" }),
    );
  }
}

export function paintLibraryTree(host: HTMLElement, prefix: string, items: KbBook[], ctx: LibraryTreeCtx): void {
  host.replaceChildren();
  const extra: { id: string; label: string }[] = [];
  if (items.some((b) => stageOf(b) === "ps")) extra.push({ id: "ps", label: "小学" });
  if (items.some((b) => stageOf(b) === "other")) extra.push({ id: "other", label: "其他学段" });
  for (const stage of [...STAGES, ...extra]) {
    const hit = items.filter((b) => stageOf(b) === stage.id);
    host.appendChild(
      renderFolder(`${prefix}/${stage.id}`, stage.label, hit.length, (body) => {
        renderSubjectFolders(body, `${prefix}/${stage.id}`, hit, ctx);
      }, ctx, { kind: "stage" }),
    );
  }
}

export function paintNodeExpanded(node: HTMLElement, on: boolean): void {
  node.classList.toggle("is-expanded", on);
  const tocHost = node.querySelector<HTMLElement>(".kb-node-toc");
  const caretBtn = node.querySelector<HTMLButtonElement>(".kb-caret-btn");
  if (caretBtn) {
    caretBtn.setAttribute("aria-expanded", on ? "true" : "false");
    caretBtn.setAttribute("aria-label", on ? "收起目录" : "展开目录");
  }
  tocHost?.classList.toggle("hidden", !on);
}
