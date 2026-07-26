/**
 * Shared TOC / print-page navigation for MinerU import preview and textbook reader.
 * Rows use data-goto-page (PDF page) or data-goto-print-page (book page number).
 */

export type TocNavScrollers = {
  /** Scroll to PDF page_no (1-based). */
  scrollToPage: (pageNo: number) => boolean | void | Promise<boolean | void>;
  /** Scroll to article[data-print-page]. Optional if only PDF pages are used. */
  scrollToPrintPage?: (printPage: number) => boolean | void | Promise<boolean | void>;
};

/**
 * Bind click/keydown on a scroll container so `.lb-toc-row` and footer
 * `.lb-page-number--toc-link` jump like the import ribbon.
 */
export function bindTextbookTocNav(
  root: HTMLElement,
  scrollers: TocNavScrollers,
): () => void {
  const onClick = (ev: MouseEvent) => {
    const t = ev.target as HTMLElement | null;
    if (!t) return;

    const pageNum = t.closest?.<HTMLElement>(".lb-page-number--toc-link");
    if (pageNum && root.contains(pageNum)) {
      ev.preventDefault();
      ev.stopPropagation();
      const pdfPage = Number(pageNum.getAttribute("data-goto-page") || 0);
      if (pdfPage > 0) void scrollers.scrollToPage(pdfPage);
      return;
    }

    const row = t.closest?.<HTMLElement>(
      ".lb-toc-row[data-goto-print-page], .lb-toc-row[data-goto-page], [data-goto-page], [data-goto-print-page]",
    );
    if (!row || !root.contains(row)) return;
    if (row.classList.contains("lb-page-number")) return;
    ev.preventDefault();
    ev.stopPropagation();
    const pdfPage = Number(row.getAttribute("data-goto-page") || 0);
    const printPage = Number(row.getAttribute("data-goto-print-page") || 0);
    if (pdfPage > 0) {
      void scrollers.scrollToPage(pdfPage);
      return;
    }
    if (printPage > 0 && scrollers.scrollToPrintPage) {
      void scrollers.scrollToPrintPage(printPage);
    }
  };

  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    const t = ev.target as HTMLElement | null;
    if (
      t?.classList?.contains("lb-toc-row") ||
      t?.classList?.contains("lb-page-number--toc-link")
    ) {
      t.click();
      ev.preventDefault();
    }
  };

  root.addEventListener("click", onClick);
  root.addEventListener("keydown", onKey);
  return () => {
    root.removeEventListener("click", onClick);
    root.removeEventListener("keydown", onKey);
  };
}

/** Mark footer page numbers as "back to TOC" on non-TOC pages. */
export function decoratePageNumbersBackToToc(root: HTMLElement): void {
  const toc = root.querySelector<HTMLElement>(".lb-toc-wrap, .lb-toc");
  let tocPageNo = 0;
  if (toc) {
    const wrap =
      toc.closest<HTMLElement>("[data-page-no]") ||
      toc.closest<HTMLElement>(".import-page-block");
    tocPageNo = Number(wrap?.dataset.pageNo || 0);
    if (!tocPageNo) {
      const article = toc.closest<HTMLElement>(".lb-page");
      tocPageNo = Number(article?.getAttribute("data-page") || 0);
    }
  }
  root.querySelectorAll<HTMLElement>(".lb-page").forEach((article) => {
    const body = article.closest<HTMLElement>("[data-page-no]") || article.parentElement;
    const isTocPage = !!article.querySelector(".lb-toc-wrap, .lb-toc");
    article.querySelectorAll<HTMLElement>(".lb-page-number").forEach((el) => {
      if (isTocPage || tocPageNo <= 0) {
        el.classList.remove("lb-page-number--toc-link");
        el.removeAttribute("data-goto-page");
        el.removeAttribute("title");
        el.removeAttribute("role");
        el.removeAttribute("tabindex");
        return;
      }
      el.classList.add("lb-page-number--toc-link");
      el.title = "返回目录页";
      el.setAttribute("role", "link");
      el.tabIndex = 0;
      el.setAttribute("data-goto-page", String(tocPageNo));
    });
    void body;
  });
}
