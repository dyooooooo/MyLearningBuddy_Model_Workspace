export type BookSortId =
  | "updated-desc"
  | "updated-asc"
  | "created-desc"
  | "created-asc"
  | "title-asc"
  | "title-desc";

export type BookSortRow = {
  id: BookSortId;
  group: string;
  label: string;
};

export const BOOK_SORTS: BookSortRow[] = [
  { id: "updated-desc", group: "时间", label: "最近更新" },
  { id: "updated-asc", group: "时间", label: "最早更新" },
  { id: "created-desc", group: "导入", label: "最近导入" },
  { id: "created-asc", group: "导入", label: "最早导入" },
  { id: "title-asc", group: "标题", label: "A → Z" },
  { id: "title-desc", group: "标题", label: "Z → A" },
];

export const DEFAULT_BOOK_SORT: BookSortId = "updated-desc";

export type SortableBook = {
  label?: string;
  filename?: string;
  created_at?: string;
  updated_at?: string;
};

export function parseBookSort(raw: unknown): BookSortId {
  const id = String(raw || "");
  return BOOK_SORTS.some((row) => row.id === id) ? (id as BookSortId) : DEFAULT_BOOK_SORT;
}

export function bookSortLabel(id: BookSortId): string {
  const row = BOOK_SORTS.find((item) => item.id === id);
  if (!row) return "排序";
  return row.group === "标题" ? `标题 ${row.label}` : row.label;
}

export function titleOf(book: SortableBook): string {
  return (book.label || book.filename || "").trim();
}

function timeMs(raw: string | undefined): number {
  const n = Date.parse(raw || "");
  return Number.isFinite(n) ? n : 0;
}

export function compareBooks(a: SortableBook, b: SortableBook, sort: BookSortId): number {
  if (sort.startsWith("title-")) {
    const cmp = titleOf(a).localeCompare(titleOf(b), "zh");
    return sort === "title-desc" ? -cmp : cmp;
  }
  const field = sort.startsWith("created") ? "created_at" : "updated_at";
  const cmp = timeMs(a[field]) - timeMs(b[field]);
  if (cmp) return sort.endsWith("desc") ? -cmp : cmp;
  return titleOf(a).localeCompare(titleOf(b), "zh");
}

export function sortBooks<T extends SortableBook>(items: T[], sort: BookSortId): T[] {
  return [...items].sort((a, b) => compareBooks(a, b, sort));
}
