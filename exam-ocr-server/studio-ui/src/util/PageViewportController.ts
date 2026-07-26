/**
 * Viewport-driven page loading: shell first, fetch ±radius, typeset only when visible.
 */

export type PageViewportOptions = {
  root: HTMLElement;
  pageCount: number;
  radius?: number;
  /** Build/ensure a page shell element for pageNo (1-based). */
  ensureShell: (pageNo: number) => HTMLElement;
  /** Load HTML into shell when approaching viewport. */
  loadPage: (pageNo: number, shell: HTMLElement) => Promise<void>;
  /** Optional: after load, schedule math typeset for this shell. */
  typeset?: (shell: HTMLElement) => void;
  onActivePage?: (pageNo: number) => void;
};

export type PageViewportHandle = {
  destroy: () => void;
  refresh: () => void;
  scrollToPage: (pageNo: number) => void;
};

export function createPageViewportController(opts: PageViewportOptions): PageViewportHandle {
  const radius = opts.radius ?? 2;
  const loaded = new Set<number>();
  const loading = new Set<number>();
  let destroyed = false;
  let observer: IntersectionObserver | null = null;

  function shells(): HTMLElement[] {
    return Array.from(opts.root.querySelectorAll<HTMLElement>("[data-page-no]"));
  }

  async function ensureLoaded(pageNo: number): Promise<void> {
    if (destroyed || pageNo < 1 || pageNo > opts.pageCount) return;
    if (loaded.has(pageNo) || loading.has(pageNo)) return;
    loading.add(pageNo);
    try {
      const shell = opts.ensureShell(pageNo);
      await opts.loadPage(pageNo, shell);
      if (destroyed) return;
      loaded.add(pageNo);
      if (opts.typeset) {
        const run = () => {
          if (!destroyed) opts.typeset?.(shell);
        };
        if (typeof requestIdleCallback === "function") {
          requestIdleCallback(() => run(), { timeout: 800 });
        } else {
          window.setTimeout(run, 0);
        }
      }
    } finally {
      loading.delete(pageNo);
    }
  }

  function prefetchAround(center: number): void {
    const lo = Math.max(1, center - radius);
    const hi = Math.min(opts.pageCount, center + radius);
    for (let p = lo; p <= hi; p++) void ensureLoaded(p);
  }

  observer = new IntersectionObserver(
    (entries) => {
      let best: { pageNo: number; ratio: number } | null = null;
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        const pageNo = Number((en.target as HTMLElement).dataset.pageNo || 0);
        if (!pageNo) continue;
        void ensureLoaded(pageNo);
        if (!best || en.intersectionRatio > best.ratio) {
          best = { pageNo, ratio: en.intersectionRatio };
        }
      }
      if (best) {
        prefetchAround(best.pageNo);
        opts.onActivePage?.(best.pageNo);
      }
    },
    { root: opts.root, rootMargin: "120px 0px", threshold: [0.05, 0.25, 0.5] },
  );

  function observeAll(): void {
    for (const el of shells()) observer?.observe(el);
  }

  // Initial shells for all pages (lightweight placeholders).
  for (let p = 1; p <= opts.pageCount; p++) {
    const shell = opts.ensureShell(p);
    if (!shell.dataset.pageNo) shell.dataset.pageNo = String(p);
  }
  observeAll();
  prefetchAround(1);

  return {
    destroy: () => {
      destroyed = true;
      observer?.disconnect();
      observer = null;
    },
    refresh: () => {
      observeAll();
    },
    scrollToPage: (pageNo: number) => {
      const shell = opts.ensureShell(pageNo);
      prefetchAround(pageNo);
      shell.scrollIntoView({ block: "start", behavior: "smooth" });
    },
  };
}
