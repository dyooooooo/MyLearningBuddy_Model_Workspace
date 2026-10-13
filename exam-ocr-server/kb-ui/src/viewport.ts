export type KbViewportOpts = {
  root: HTMLElement;
  pageCount: number;
  radius?: number;
  ensureShell: (pageNo: number) => HTMLElement;
  loadPage: (pageNo: number, shell: HTMLElement) => Promise<void>;
  unloadPage?: (pageNo: number, shell: HTMLElement) => void;
  onActivePage?: (pageNo: number) => void;
};

export type KbViewport = {
  destroy: () => void;
  scrollToPage: (pageNo: number) => Promise<void>;
  getActivePage: () => number;
  refresh: () => void;
};

/**
 * 滚到目标页，并在版面落定后再校一次。
 *
 * 目标页滚过去之后，邻页才开始填内容、对照格子才换上原图，页高一变目标页
 * 就被顶走（打开书会看着像「又回到第 1 页」）。等一帧、再等一小会各校一次。
 */
export async function settleScroll(view: KbViewport, pageNo: number): Promise<void> {
  await view.scrollToPage(pageNo);
  await new Promise<void>((done) => window.requestAnimationFrame(() => done()));
  if (view.getActivePage() !== pageNo) await view.scrollToPage(pageNo);
  await new Promise<void>((done) => window.setTimeout(done, 180));
  if (view.getActivePage() !== pageNo) await view.scrollToPage(pageNo);
}

export function mountKbViewport(opts: KbViewportOpts): KbViewport {
  const loaded = new Set<number>();
  const loading = new Set<number>();
  let active = 1;
  let destroyed = false;
  let observer: IntersectionObserver | null = null;

  function shells(): HTMLElement[] {
    return [...opts.root.querySelectorAll<HTMLElement>(".kb-page-block[data-page-no]")];
  }

  function visiblePage(): number {
    const list = shells();
    if (!list.length) return 1;
    const rootRect = opts.root.getBoundingClientRect();
    const top = Math.max(rootRect.top, 0);
    const bottom = Math.min(rootRect.bottom, window.innerHeight);
    const anchor = top + Math.max(40, (bottom - top) * 0.28);
    let candidate = Number(list[0].dataset.pageNo || 1);
    for (const el of list) {
      const n = Number(el.dataset.pageNo || 0);
      if (!n) continue;
      if (el.getBoundingClientRect().top <= anchor) candidate = n;
      else break;
    }
    return candidate;
  }

  async function load(pageNo: number): Promise<void> {
    if (destroyed || pageNo < 1 || pageNo > opts.pageCount) return;
    if (loaded.has(pageNo) || loading.has(pageNo)) return;
    loading.add(pageNo);
    const shell = opts.ensureShell(pageNo);
    try {
      await opts.loadPage(pageNo, shell);
      const body = shell.querySelector(".kb-page-body");
      if (body && !body.classList.contains("is-unloaded")) loaded.add(pageNo);
    } finally {
      loading.delete(pageNo);
    }
  }

  function setActive(pageNo: number): void {
    if (pageNo === active) return;
    active = pageNo;
    opts.onActivePage?.(pageNo);
  }

  function bindObserver(): void {
    observer?.disconnect();
    observer = new IntersectionObserver(
      (entries) => {
        if (destroyed) return;
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const n = Number((entry.target as HTMLElement).dataset.pageNo || 0);
          if (n) void load(n);
        }
        setActive(visiblePage());
      },
      { root: opts.root, rootMargin: "600px 0px", threshold: 0.01 },
    );
    for (const el of shells()) observer.observe(el);
  }

    void (async () => {
    const first = visiblePage();
    setActive(first);
    await load(first);
    const neighbors = [first - 1, first + 1].filter((n) => n >= 1 && n <= opts.pageCount);
    void Promise.all(neighbors.map((n) => load(n)));
    bindObserver();
  })();

  return {
    destroy: () => {
      destroyed = true;
      observer?.disconnect();
      observer = null;
      loaded.clear();
    },
    scrollToPage: async (pageNo: number) => {
      const n = Math.max(1, Math.min(opts.pageCount, pageNo));
      const shell = opts.ensureShell(n);
      observer?.observe(shell);
      await load(n);
      shell.scrollIntoView({ block: "start" });
      setActive(n);
    },
    getActivePage: () => active,
    refresh: () => {
      if (destroyed) return;
      bindObserver();
      void load(visiblePage());
    },
  };
}
