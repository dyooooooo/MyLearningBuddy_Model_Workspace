import { renderIrPage } from "./lib/render-page.mjs";

export function paint(payload = {}, _ctx = {}) {
  const page = payload.page && typeof payload.page === "object" ? payload.page : {};
  const bookId = String(payload.book_id || payload.bookId || "");
  return renderIrPage(page, bookId);
}
