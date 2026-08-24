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

  bindObserver();
  void (async () => {
    const first = visiblePage();
    setActive(first);
    const warm = [first - 1, first, first + 1, first + 2, first + 3].filter(
      (n) => n >= 1 && n <= opts.pageCount,
    );
    await Promise.all(warm.map((n) => load(n)));
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
