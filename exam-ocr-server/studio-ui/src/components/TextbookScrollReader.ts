/**
 * Continuous textbook reader — stacked PDF pages with scroll ↔ left-tree sync.
 * Does NOT call openNode on scroll (avoids ribbon steal + full remount flicker).
 */

import { corpus } from "../api/corpus";
import type { CorpusNode } from "../api/types";
import { store } from "../state/store";
import { el } from "../util/dom";
import { isHtmlContent, sanitizeHtml, TEXTBOOK_CSS_HREF } from "../util/html-sanitize";
import { typesetTextbookMathIdle } from "../util/math-render";
import {
  bindTextbookTocNav,
  decoratePageNumbersBackToToc,
} from "../util/textbook-toc-nav";
import { mountStickyPageStrip } from "./import/StickyPageStrip";

export interface ReaderPage {
  id: string;
  label: string;
  source_page: number;
  section_id?: string;
  status?: string;
  import_job_id?: string;
}

const READER_CLS = "textbook-scroll-reader";
const BLOCK_FLASH_CLS = "reader-heading-flash";
const BLOCK_FLASH_MS = 2600;

let cssLinked = false;
function ensureTextbookCss(): void {
  if (cssLinked) return;
  if (document.querySelector(`link[href="${TEXTBOOK_CSS_HREF}"]`)) {
    cssLinked = true;
    return;
  }
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = TEXTBOOK_CSS_HREF;
  document.head.appendChild(link);
  cssLinked = true;
}

function highlightTree(nodeId: string): void {
  store.markSelected(nodeId);
  document.dispatchEvent(
    new CustomEvent("studio:tree-highlight", { detail: { nodeId } }),
  );
}

function focusBlockId(node: CorpusNode): string {
  const ids = node.meta?.block_ids;
  if (Array.isArray(ids) && ids.length > 0) {
    const first = String(ids[0] || "").trim();
    if (first) return first;
  }
  return "";
}

function focusPageNo(node: CorpusNode): number {
  const meta = node.meta || {};
  const start = Number(meta.start_page || 0);
  if (start > 0) return start;
  const sp = Number(meta.source_page || 0);
  if (sp > 0) return sp;
  if (Array.isArray(meta.source_pages) && meta.source_pages.length) {
    const n = Number(meta.source_pages[0]);
    if (n > 0) return n;
  }
  return 0;
}

export function textbookIdOf(node: CorpusNode): string {
  if (node.node_type === "textbook") return node.id;
  return String((node.links as Record<string, string>)?.textbook_id || node.meta?.textbook_id || "");
}

export function isTextbookReadable(node: CorpusNode): boolean {
  if (node.node_type === "textbook" || node.node_type === "section") return true;
  if (node.node_type === "chunk") return !!textbookIdOf(node);
  return false;
}

export interface TextbookScrollHandle {
  textbookId: string;
  scrollToFocus: (node: CorpusNode) => void;
  destroy: () => void;
}

export async function mountTextbookScrollReader(
  host: HTMLElement,
  focus: CorpusNode,
): Promise<TextbookScrollHandle | null> {
  const tbId = textbookIdOf(focus);
  if (!tbId) return null;

  ensureTextbookCss();
  host.innerHTML = "";
  host.classList.add(READER_CLS);

  const bar = el("div", "textbook-reader-bar");
  bar.appendChild(el("span", "muted", "连续滚动浏览 · 左侧目录随滚动高亮"));
  const editHint = el("button", "btn-link", "编辑当前页") as HTMLButtonElement;
  editHint.type = "button";
  bar.appendChild(editHint);
  host.appendChild(bar);

  const pageStrip = mountStickyPageStrip({
    onGoto: (pageNo) => {
      void (async () => {
        await ensureWindowAround(pageNo);
        scrollToPageNo(pageNo);
        pageStrip.setActive(pageNo);
      })();
    },
  });
  host.appendChild(pageStrip.el);

  const scroller = el("div", "textbook-reader-pages");
  host.appendChild(scroller);

  const loading = el("p", "muted", "加载页列表…");
  scroller.appendChild(loading);

  let pages: ReaderPage[] = [];
  try {
    pages = (await corpus.readerPages(focus.id)) as ReaderPage[];
  } catch (e) {
    loading.textContent = `无法加载页列表：${(e as Error).message}`;
    return {
      textbookId: tbId,
      scrollToFocus: () => undefined,
      destroy: () => {
        host.innerHTML = "";
        host.classList.remove(READER_CLS);
      },
    };
  }

  if (!pages.length) {
    loading.textContent = "暂无页内容（导入完成后将在此连续滚动显示）";
    return {
      textbookId: tbId,
      scrollToFocus: () => undefined,
      destroy: () => {
        host.innerHTML = "";
        host.classList.remove(READER_CLS);
      },
    };
  }
  loading.remove();

  const pageByNo = new Map(pages.map((p) => [p.source_page, p]));
  const pageBodies = new Map<number, HTMLElement>();
  const pageReady = new Map<number, Promise<void>>();
  let activePageNo = 0;
  let highlightObserver: IntersectionObserver | null = null;
  let loadObserver: IntersectionObserver | null = null;
  let destroyed = false;
  let pinTreeUntil = 0;
  let flashTimer: number | null = null;

  /** Prefetch this many pages on each side of the focus page. */
  const PREFETCH_RADIUS = 2;

  function syncHighlight(pageNo: number): void {
    if (pageNo <= 0 || pageNo === activePageNo) return;
    // While a TOC click is settling, keep the clicked node selected.
    if (Date.now() < pinTreeUntil) return;
    activePageNo = pageNo;
    pageStrip.setActive(pageNo);
    const page = pageByNo.get(pageNo);
    if (!page) return;
    const target = (page.section_id && !page.section_id.endsWith(":pages")
      ? page.section_id
      : page.id);
    highlightTree(target);
    editHint.onclick = () => {
      void store.openNode(page.id);
    };
  }

  pageStrip.setPages(pages.map((p) => p.source_page).filter((n) => n > 0));

  function ensurePageContent(pageNo: number): Promise<void> {
    const existing = pageReady.get(pageNo);
    if (existing) return existing;
    const p = pageByNo.get(pageNo);
    const body = pageBodies.get(pageNo);
    if (!p || !body) return Promise.resolve();

    const ready = (async () => {
      try {
        const node = await corpus.getNode(p.id);
        if (destroyed) return;
        const html = node.full_text || node.summary || "";
        if (isHtmlContent(html, node.meta as Record<string, unknown>)) {
          body.innerHTML = sanitizeHtml(html);
          typesetTextbookMathIdle(body);
        } else {
          body.textContent = html || "（空页）";
        }
        // Needs full scroller so TOC page can be found for page-number links.
        decoratePageNumbersBackToToc(scroller);
      } catch (e) {
        if (!destroyed) body.textContent = `加载失败：${(e as Error).message}`;
      }
    })();
    pageReady.set(pageNo, ready);
    return ready;
  }

  async function ensureWindowAround(pageNo: number): Promise<void> {
    if (pageNo <= 0) return;
    const idxs = pages
      .map((p, i) => ({ p, i }))
      .filter(({ p }) => Math.abs(p.source_page - pageNo) <= PREFETCH_RADIUS)
      .map(({ p }) => p.source_page);
    // Also include exact neighbors by index when page numbers are sparse.
    const center = pages.findIndex((p) => p.source_page === pageNo);
    if (center >= 0) {
      for (let i = Math.max(0, center - PREFETCH_RADIUS); i <= Math.min(pages.length - 1, center + PREFETCH_RADIUS); i++) {
        idxs.push(pages[i].source_page);
      }
    }
    await Promise.all([...new Set(idxs)].map((n) => ensurePageContent(n)));
  }

  function bindObservers(): void {
    highlightObserver?.disconnect();
    loadObserver?.disconnect();

    highlightObserver = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        const top = visible[0]?.target as HTMLElement | undefined;
        const pageNo = Number(top?.dataset.pageNo || 0);
        if (pageNo > 0) syncHighlight(pageNo);
      },
      { root: scroller, threshold: [0.2, 0.45, 0.7] },
    );

    // Lazy-load page HTML when near viewport (don't block first paint on full book).
    loadObserver = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const pageNo = Number((e.target as HTMLElement).dataset.pageNo || 0);
          if (pageNo > 0) void ensureWindowAround(pageNo);
        }
      },
      { root: scroller, rootMargin: "600px 0px", threshold: 0.01 },
    );

    scroller.querySelectorAll<HTMLElement>("[data-page-no]").forEach((n) => {
      highlightObserver?.observe(n);
      loadObserver?.observe(n);
    });
  }

  // Shells only — content loads lazily around focus / scroll.
  for (const p of pages) {
    const wrap = el("div", "import-page-block textbook-reader-page");
    wrap.dataset.pageNo = String(p.source_page);
    wrap.dataset.nodeId = p.id;
    if (p.section_id) wrap.dataset.sectionId = p.section_id;

    const tag = el("div", "import-page-tag muted", `第 ${p.source_page} 页`);
    wrap.appendChild(tag);
    const body = el("div", "lb-page textbook-preview");
    body.textContent = "加载中…";
    wrap.appendChild(body);
    scroller.appendChild(wrap);
    pageBodies.set(p.source_page, body);
  }
  bindObservers();

  function clearBlockFlash(): void {
    scroller.querySelectorAll(`.${BLOCK_FLASH_CLS}`).forEach((n) => {
      n.classList.remove(BLOCK_FLASH_CLS);
    });
    if (flashTimer != null) {
      window.clearTimeout(flashTimer);
      flashTimer = null;
    }
  }

  function flashBlock(target: HTMLElement): void {
    clearBlockFlash();
    target.classList.add(BLOCK_FLASH_CLS);
    flashTimer = window.setTimeout(() => {
      target.classList.remove(BLOCK_FLASH_CLS);
      flashTimer = null;
    }, BLOCK_FLASH_MS);
  }

  function scrollElementToTop(target: HTMLElement): void {
    const top =
      target.getBoundingClientRect().top -
      scroller.getBoundingClientRect().top +
      scroller.scrollTop;
    scroller.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
  }

  function scrollToPageNo(pageNo: number): boolean {
    const block = scroller.querySelector<HTMLElement>(`[data-page-no="${pageNo}"]`);
    if (!block) return false;
    scrollElementToTop(block);
    activePageNo = pageNo;
    block.classList.add("import-page-block--focus");
    window.setTimeout(() => block.classList.remove("import-page-block--focus"), 1200);
    return true;
  }

  function scrollToPrintPage(printPage: number): boolean {
    const article = scroller.querySelector<HTMLElement>(
      `.lb-page[data-print-page="${printPage}"]`,
    );
    if (!article) return false;
    const wrap =
      article.closest<HTMLElement>("[data-page-no]") ||
      article.closest<HTMLElement>(".import-page-block") ||
      article;
    const pageNo = Number(
      (wrap as HTMLElement).dataset?.pageNo || article.getAttribute("data-page") || 0,
    );
    if (pageNo > 0) return scrollToPageNo(pageNo);
    scrollElementToTop(wrap as HTMLElement);
    return true;
  }

  const unbindTocNav = bindTextbookTocNav(scroller, {
    scrollToPage: scrollToPageNo,
    scrollToPrintPage,
  });

  function findBlockEl(pageNo: number, blockId: string): HTMLElement | null {
    if (!blockId) return null;
    const page = scroller.querySelector<HTMLElement>(`[data-page-no="${pageNo}"]`);
    if (!page) return null;
    try {
      return page.querySelector<HTMLElement>(`[data-block-id="${CSS.escape(blockId)}"]`);
    } catch {
      return page.querySelector<HTMLElement>(`[data-block-id="${blockId}"]`);
    }
  }

  async function scrollToFocusAsync(node: CorpusNode): Promise<void> {
    if (destroyed) return;
    pinTreeUntil = Date.now() + 1800;
    highlightTree(node.id);

    if (node.node_type === "textbook") {
      scroller.scrollTo({ top: 0, behavior: "smooth" });
      if (pages[0]) {
        activePageNo = pages[0].source_page;
      }
      return;
    }

    let pageNo = focusPageNo(node);
    if (pageNo <= 0 && node.node_type === "section") {
      const hit = pages.find((p) => p.section_id === node.id);
      if (hit) pageNo = hit.source_page;
    }
    if (pageNo <= 0 && node.node_type === "chunk") {
      const sec = String((node.links as Record<string, string>)?.section_id || "");
      if (sec) {
        const hit = pages.find((p) => p.section_id === sec);
        if (hit) pageNo = hit.source_page;
      }
    }
    if (pageNo <= 0) return;

    await ensureWindowAround(pageNo);
    if (destroyed) return;

    const blockId = focusBlockId(node);
    const blockEl = findBlockEl(pageNo, blockId);
    if (blockEl) {
      scrollElementToTop(blockEl);
      flashBlock(blockEl);
      activePageNo = pageNo;
      return;
    }

    // Fallback: page top (no block id / HTML without data-block-id)
    scrollToPageNo(pageNo);
  }

  function scrollToFocus(node: CorpusNode): void {
    void scrollToFocusAsync(node);
  }

  editHint.onclick = () => {
    const cur = pages.find((p) => p.source_page === activePageNo) || pages[0];
    if (cur) void store.openNode(cur.id);
  };

  scrollToFocus(focus);

  return {
    textbookId: tbId,
    scrollToFocus,
    destroy: () => {
      destroyed = true;
      clearBlockFlash();
      unbindTocNav();
      highlightObserver?.disconnect();
      loadObserver?.disconnect();
      highlightObserver = null;
      loadObserver = null;
      host.innerHTML = "";
      host.classList.remove(READER_CLS);
    },
  };
}
