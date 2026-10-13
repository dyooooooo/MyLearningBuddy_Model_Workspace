import { renderIrPage } from "./lib/render-page.mjs";
import { loadBookPages, loadPrintToc } from "./lib/load-book-content.mjs";
import { printTocPage } from "./lib/print-toc.mjs";

export function paint(payload = {}, _ctx = {}) {
  const page = payload.page && typeof payload.page === "object" ? payload.page : {};
  const bookId = String(payload.book_id || payload.bookId || "");
  const printToc = payload.print_toc && typeof payload.print_toc === "object"
    ? payload.print_toc
    : loadPrintToc(bookId);
  if (printTocPage(printToc, page.page)) return renderIrPage(page, { bookId, printToc });
  const bookPages = Array.isArray(payload.book_pages) && payload.book_pages.length
    ? payload.book_pages
    : loadBookPages(bookId);
  return renderIrPage(page, { bookId, bookPages, printToc });
}
