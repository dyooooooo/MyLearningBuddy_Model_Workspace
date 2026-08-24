/**
 * Viewport-driven page loading: light shells for 1..N, fetch ±radius,
 * unload far pages (keep shell) so DOM stays bounded.
 *
 * Active page uses a scroll-anchor + hysteresis so the boundary between
 * two pages does not oscillate (which used to thrash load/unload and jitter).
 *
 * Programmatic jump (`scrollToPage`) pins the target and re-aligns after
 * neighbor loads — otherwise placeholder→real height changes above the
 * target shove the viewport to the wrong page.
 *
 * Scroll + pin: if the user keeps scrolling away from the pin, pin is released.
 * Visible / near-viewport shells are always loaded (IO alone is not enough:
 * unloading a still-intersecting shell does not re-fire IntersectionObserver).
 *
 * Retry rules:
 * - `loadPage === false` is retryable (draft not ready / transient error).
 * - Prefetch window always clears deferred so navigation re-attempts load.
 * - `isFilled` repairs “marked loaded but DOM empty” desync (blank page).
 * - Active page and near-viewport pages are never unloaded.
 */

export type PageViewportOptions = {
  root: HTMLElement;
  pageCount: number;
  /** Prefetch / keep window half-width (default 2 → ~5 hot pages). */
  radius?: number;
  /** Keep loaded window; defaults to max(radius, 4). */
  keepRadius?: number;
  /** Build/ensure a page shell element for pageNo (1-based). */
  ensureShell: (pageNo: number) => HTMLElement;
  /**
   * Load HTML into shell when approaching viewport.
   * Return `false` to keep the page unloadable/retryable (e.g. draft not ready).
   */
  loadPage: (pageNo: number, shell: HTMLElement) => Promise<boolean | void>;
  /** Unload content but keep shell (required for DOM bound). */
  unloadPage?: (pageNo: number, shell: HTMLElement) => void;
  /** Optional: true when shell still has real content (desync repair). */
  isFilled?: (pageNo: number, shell: HTMLElement) => boolean;
  /** Optional: after load, schedule math typeset for this shell. */
  typeset?: (shell: HTMLElement) => void;
  onActivePage?: (pageNo: number) => void;
  /** When true, never unload (e.g. compare mode — blanks stick after stop-scroll). */
  shouldUnload?: () => boolean;
};

export type PageViewportHandle = {
  destroy: () => void;
  refresh: () => void;
  scrollToPage: (pageNo: number) => void;
  setPageCount: (n: number) => void;
  invalidate: (pageNo?: number) => void;
  isLoaded: (pageNo: number) => boolean;
  getActivePage: () => number;
  /** Force-load any blank shells currently near the viewport (even without scroll). */
  healVisible: () => void;
};

/** Sticky reading line inside the scroll root (fraction of viewport height). */
const ANCHOR_FRAC = 0.28;
/** Extra deadband (px) before allowing ±1 page switch. */
const HYSTERESIS_PX = 48;
/** Keep jump target pinned this long after last re-align. */
const PIN_MS = 450;
/** Prefetch margin beyond the visible root (px). */
const VISIBLE_MARGIN_PX = 480;
/** User scrolled this many pages away from pin → release pin. */
const PIN_BREAK_PAGES = 2;

export function createPageViewportController(opts: PageViewportOptions): PageViewportHandle {
  const radius = opts.radius ?? 2;
  const keepRadius = opts.keepRadius ?? Math.max(radius, 4);
  let pageCount = Math.max(0, opts.pageCount | 0);
  const loaded = new Set<number>();
  const loading = new Set<number>();
  /** loadPage returned false — retry when page re-enters prefetch window. */
  const deferred = new Set<number>();
  let destroyed = false;
  let observer: IntersectionObserver | null = null;
  let activePage = 0;
  let unloadTimer: ReturnType<typeof setTimeout> | null = null;
  let scrollRaf = 0;
  /** Programmatic jump target; suppress scroll-derived active + re-align on load. */
  let pinPage = 0;
  let pinUntil = 0;
  let pinClearTimer: ReturnType<typeof setTimeout> | null = null;
  let realignRaf = 0;

  function shells(): HTMLElement[] {
    // Only page cards — avoid sticky strip / nested duplicates.
    return Array.from(
      opts.root.querySelectorAll<HTMLElement>(".import-page-block[data-page-no]"),
    ).sort((a, b) => Number(a.dataset.pageNo || 0) - Number(b.dataset.pageNo || 0));
  }

  function shellFor(pageNo: number): HTMLElement | null {
    return (
      opts.root.querySelector<HTMLElement>(
        `.import-page-block[data-page-no="${pageNo}"]`,
      ) || null
    );
  }

  function shellLooksFilled(pageNo: number, shell: HTMLElement): boolean {
    if (opts.isFilled) return opts.isFilled(pageNo, shell);
    return !shell.classList.contains("is-shell");
  }

  function isPinned(): boolean {
    return pinPage > 0 && performance.now() < pinUntil;
  }

  function clearPin(): void {
    pinPage = 0;
    pinUntil = 0;
    if (pinClearTimer != null) {
      clearTimeout(pinClearTimer);
      pinClearTimer = null;
    }
  }

  function bumpPin(pageNo: number): void {
    pinPage = pageNo;
    pinUntil = performance.now() + PIN_MS;
    if (pinClearTimer != null) clearTimeout(pinClearTimer);
    pinClearTimer = setTimeout(() => {
      pinClearTimer = null;
      if (performance.now() >= pinUntil) pinPage = 0;
    }, PIN_MS + 40);
  }

  /**
   * Shell overlaps the scroll root (with margin) — must stay loaded.
   * In compare mode the page card can be short while the row (image) is tall;
   * use the compare row rect so a blank left cell still counts as "in view".
   */
  function shellNearViewport(shell: HTMLElement, margin = VISIBLE_MARGIN_PX): boolean {
    const rootRect = opts.root.getBoundingClientRect();
    const el =
      shell.closest<HTMLElement>(".import-compare-row") || shell;
    const r = el.getBoundingClientRect();
    return r.bottom >= rootRect.top - margin && r.top <= rootRect.bottom + margin;
  }

  /** Align page card top to scroll root top (instant; no smooth drift). */
  function alignShellToTop(pageNo: number): void {
    if (destroyed || pageNo < 1) return;
    const shell = shellFor(pageNo) || opts.ensureShell(pageNo);
    const root = opts.root;
    const rootRect = root.getBoundingClientRect();
    const shellRect = shell.getBoundingClientRect();
    const delta = shellRect.top - rootRect.top;
    if (Math.abs(delta) < 1) return;
    root.scrollTop = Math.max(0, root.scrollTop + delta);
  }

  function scheduleRealign(pageNo: number): void {
    if (destroyed || pageNo < 1) return;
    if (realignRaf) cancelAnimationFrame(realignRaf);
    realignRaf = requestAnimationFrame(() => {
      realignRaf = 0;
      if (destroyed) return;
      if (pinPage === pageNo || activePage === pageNo) {
        alignShellToTop(pageNo);
        bumpPin(pageNo);
      }
    });
  }

  /** Remember height before unload so scroll position stays stable. */
  function preserveShellHeight(shell: HTMLElement): void {
    const h = Math.round(shell.getBoundingClientRect().height);
    if (h > 80) {
      shell.style.minHeight = `${h}px`;
      shell.dataset.preservedH = String(h);
    }
  }

  async function ensureLoaded(pageNo: number): Promise<void> {
    if (destroyed || pageNo < 1) return;
    // Shell may exist before pageCount catches up (jump / compare wrap).
    if (pageNo > pageCount) {
      const prev = pageCount;
      pageCount = pageNo;
      if (pageNo > prev) ensureShellRange(Math.max(1, prev + 1), pageNo);
    }
    if (loading.has(pageNo)) return;

    const shell0 = shellFor(pageNo) || opts.ensureShell(pageNo);
    observeShell(shell0);

    if (loaded.has(pageNo)) {
      if (shellLooksFilled(pageNo, shell0)) return;
      // Desync: controller thought it was loaded but DOM is empty / placeholder.
      loaded.delete(pageNo);
    }

    // In the hot window we always retry — deferred must not stick forever.
    deferred.delete(pageNo);
    loading.add(pageNo);
    try {
      const shell = opts.ensureShell(pageNo);
      observeShell(shell);
      const ok = await opts.loadPage(pageNo, shell);
      if (destroyed) return;
      if (ok === false) {
        deferred.add(pageNo);
        loaded.delete(pageNo);
        return;
      }
      if (!shellLooksFilled(pageNo, shell)) {
        // Load claimed success but shell still empty — allow another attempt.
        loaded.delete(pageNo);
        return;
      }
      deferred.delete(pageNo);
      loaded.add(pageNo);
      // 保留卸载前高度：仅当真实内容更高时才放开，避免上方页变矮导致 scrollTop 上跳。
      if (shell.dataset.preservedH) {
        const preserved = Number(shell.dataset.preservedH || 0);
        const natural = Math.round(shell.getBoundingClientRect().height);
        if (preserved > 0 && natural + 2 >= preserved) {
          shell.style.minHeight = "";
          delete shell.dataset.preservedH;
        }
      }
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
      // 上方/本页高度变化会把 pinned 目标顶走 → 重新对齐
      if (isPinned() && pageNo <= pinPage) {
        scheduleRealign(pinPage);
      }
    } catch (e) {
      console.warn("[PageViewport] load failed", pageNo, e);
      loaded.delete(pageNo);
    } finally {
      loading.delete(pageNo);
    }
  }

  /** Load every shell currently near the viewport (IO may miss after unload). */
  function loadVisibleShells(): void {
    for (const el of shells()) {
      if (!shellNearViewport(el)) continue;
      const pageNo = Number(el.dataset.pageNo || 0);
      if (!pageNo) continue;
      if (loaded.has(pageNo) && shellLooksFilled(pageNo, el)) continue;
      void ensureLoaded(pageNo);
    }
    // Also retry deferred pages in the hot window around active/pin.
    const center = (isPinned() ? pinPage : 0) || activePage || 1;
    for (const p of [...deferred]) {
      if (Math.abs(p - center) <= radius + 2) void ensureLoaded(p);
    }
  }

  function unloadFar(center: number): void {
    if (!opts.unloadPage || center < 1) return;
    // While jump-pinning, do not unload — avoids blank “准备加载” under the viewport.
    if (isPinned()) return;
    if (opts.shouldUnload && !opts.shouldUnload()) return;
    const hi = Math.min(pageCount, center + keepRadius);
    // 上方多留一截缓冲再卸：紧邻上方卸载会改 scrollHeight 前段 → 往下滚却上跳。
    const aboveCut = Math.max(1, center - Math.max(keepRadius * 4, 10));
    for (const p of [...loaded]) {
      if (p === activePage || p === center) continue;
      if (p <= hi && p >= aboveCut) continue;
      const shell = shellFor(p) || opts.ensureShell(p);
      // Never unload what the user can still see (IO won't re-fire if it stays intersecting).
      if (shellNearViewport(shell)) continue;
      try {
        preserveShellHeight(shell);
        opts.unloadPage(p, shell);
      } catch (e) {
        console.warn("[PageViewport] unload failed", p, e);
      }
      loaded.delete(p);
      // If layout shifted and it is still near view, reload immediately.
      if (shellNearViewport(shell)) void ensureLoaded(p);
    }
  }

  function scheduleUnload(center: number): void {
    if (unloadTimer != null) clearTimeout(unloadTimer);
    unloadTimer = setTimeout(() => {
      unloadTimer = null;
      if (!destroyed) unloadFar(center);
    }, 280);
  }

  function prefetchAround(center: number): void {
    if (center < 1 || pageCount < 1) return;
    const lo = Math.max(1, center - radius);
    const hi = Math.min(pageCount, center + radius);
    for (let p = lo; p <= hi; p++) void ensureLoaded(p);
    loadVisibleShells();
    scheduleUnload(center);
  }

  /**
   * Stable active page: last page whose top is at/above the reading anchor.
   * With hysteresis on ±1 flips so the boundary does not chatter.
   */
  function pickActiveFromScroll(): number {
    const list = shells();
    if (!list.length) return 0;
    const rootRect = opts.root.getBoundingClientRect();
    const anchorY = rootRect.top + rootRect.height * ANCHOR_FRAC;

    let candidate = Number(list[0].dataset.pageNo || 0) || 0;
    for (const el of list) {
      const pageNo = Number(el.dataset.pageNo || 0);
      if (!pageNo) continue;
      const top = el.getBoundingClientRect().top;
      if (top <= anchorY) candidate = pageNo;
      else break;
    }
    if (!candidate) return 0;

    // Hysteresis: stay on current ±1 neighbor until clearly past the deadband.
    if (activePage > 0 && Math.abs(candidate - activePage) === 1) {
      const cur = shellFor(activePage);
      if (cur) {
        const r = cur.getBoundingClientRect();
        if (candidate > activePage) {
          // scrolling down — wait until current page top is well above the anchor
          if (r.top > anchorY - HYSTERESIS_PX) return activePage;
        } else {
          // scrolling up — wait until current page top is well below the anchor
          if (r.top < anchorY + HYSTERESIS_PX) return activePage;
        }
      }
    }
    return candidate;
  }

  function commitActive(next: number, force = false): void {
    if (next < 1 || destroyed) return;
    if (!force && next === activePage) return;
    activePage = next;
    prefetchAround(next);
    opts.onActivePage?.(next);
  }

  function onScroll(): void {
    if (destroyed || scrollRaf) return;
    scrollRaf = requestAnimationFrame(() => {
      scrollRaf = 0;
      if (destroyed) return;

      const scrolled = pickActiveFromScroll();
      // Always heal blanks in view first (covers unload-without-IO-refire).
      loadVisibleShells();

      if (isPinned()) {
        // User kept scrolling away from the jump target → release pin and follow.
        if (scrolled > 0 && Math.abs(scrolled - pinPage) >= PIN_BREAK_PAGES) {
          clearPin();
          commitActive(scrolled);
          return;
        }
        // Stay on pin for highlight, but still prefetch around what is on screen.
        if (scrolled > 0) {
          const lo = Math.max(1, scrolled - radius);
          const hi = Math.min(pageCount, scrolled + radius);
          for (let p = lo; p <= hi; p++) void ensureLoaded(p);
        }
        return;
      }

      if (scrolled > 0) commitActive(scrolled);
    });
  }

  // IntersectionObserver helps pre-load approaching pages (not sole mechanism).
  observer = new IntersectionObserver(
    (entries) => {
      for (const en of entries) {
        if (!en.isIntersecting) continue;
        const target = en.target as HTMLElement;
        let pageNo = Number(target.dataset.pageNo || target.dataset.comparePage || 0);
        if (!pageNo && target.classList.contains("import-compare-row")) {
          pageNo = Number(
            target.querySelector(".import-page-block")?.getAttribute("data-page-no") || 0,
          );
        }
        if (pageNo) void ensureLoaded(pageNo);
      }
    },
    { root: opts.root, rootMargin: `${VISIBLE_MARGIN_PX}px 0px`, threshold: [0, 0.01] },
  );

  opts.root.addEventListener("scroll", onScroll, { passive: true });

  function observeShell(el: HTMLElement): void {
    observer?.observe(el);
    // Compare row is tall (image); observe it so short HTML shells still trigger load.
    const row = el.closest<HTMLElement>(".import-compare-row");
    if (row) observer?.observe(row);
  }

  function observeAll(): void {
    for (const el of shells()) observeShell(el);
  }

  function ensureShellRange(from: number, to: number): void {
    for (let p = from; p <= to; p++) {
      const shell = opts.ensureShell(p);
      if (!shell.dataset.pageNo) shell.dataset.pageNo = String(p);
      observeShell(shell);
    }
  }

  // Initial shells for all pages (lightweight placeholders).
  if (pageCount > 0) {
    ensureShellRange(1, pageCount);
    observeAll();
    commitActive(1, true);
  }

  return {
    destroy: () => {
      destroyed = true;
      opts.root.removeEventListener("scroll", onScroll);
      if (scrollRaf) cancelAnimationFrame(scrollRaf);
      scrollRaf = 0;
      if (realignRaf) cancelAnimationFrame(realignRaf);
      realignRaf = 0;
      if (unloadTimer != null) clearTimeout(unloadTimer);
      unloadTimer = null;
      clearPin();
      observer?.disconnect();
      observer = null;
      loaded.clear();
      loading.clear();
      deferred.clear();
    },
    refresh: () => {
      observeAll();
      // Drop deferred so poll ticks can retry pages that failed earlier.
      deferred.clear();
      loadVisibleShells();
      if (activePage > 0) prefetchAround(activePage);
      else if (pageCount > 0) prefetchAround(1);
    },
    scrollToPage: (pageNo: number) => {
      if (pageNo < 1) return;
      // Allow jump before total is known: expand count to at least the target.
      if (pageNo > pageCount) {
        const prev = pageCount;
        pageCount = pageNo;
        if (pageNo > prev) ensureShellRange(Math.max(1, prev + 1), pageNo);
      }
      const shell = opts.ensureShell(pageNo);
      observeShell(shell);
      deferred.delete(pageNo);
      // Explicit navigation: if DOM empty, force reload even if marked loaded.
      if (loaded.has(pageNo) && !shellLooksFilled(pageNo, shell)) {
        loaded.delete(pageNo);
      }
      bumpPin(pageNo);
      commitActive(pageNo, true);
      // Instant align into the scroll root (smooth + height churn = wrong page).
      alignShellToTop(pageNo);
      void ensureLoaded(pageNo).then(() => {
        if (!destroyed && pinPage === pageNo) {
          alignShellToTop(pageNo);
          bumpPin(pageNo);
          // Neighbors finishing shortly after also change cumulative height.
          window.setTimeout(() => {
            if (!destroyed && pinPage === pageNo) alignShellToTop(pageNo);
          }, 120);
          window.setTimeout(() => {
            if (!destroyed && pinPage === pageNo) alignShellToTop(pageNo);
          }, 360);
        }
      });
    },
    setPageCount: (n: number) => {
      const next = Math.max(0, Math.floor(n));
      if (next === pageCount) return;
      const prev = pageCount;
      pageCount = next;
      if (next > prev) {
        ensureShellRange(prev + 1, next);
        if (activePage > 0) prefetchAround(activePage);
        else prefetchAround(1);
      } else if (next < prev) {
        for (const p of [...loaded]) {
          if (p > next) loaded.delete(p);
        }
        if (activePage > next) activePage = next > 0 ? next : 0;
      }
    },
    invalidate: (pageNo?: number) => {
      if (pageNo == null || pageNo <= 0) {
        loaded.clear();
        deferred.clear();
        loadVisibleShells();
        if (activePage > 0) prefetchAround(activePage);
        else if (pageCount > 0) prefetchAround(1);
        return;
      }
      loaded.delete(pageNo);
      deferred.delete(pageNo);
      const center = activePage || pageNo;
      const lo = Math.max(1, center - keepRadius);
      const hi = Math.min(pageCount, center + keepRadius);
      if ((pageNo >= lo && pageNo <= hi) || pageNo === activePage) {
        void ensureLoaded(pageNo);
      } else {
        const shell = shellFor(pageNo);
        if (shell && shellNearViewport(shell)) void ensureLoaded(pageNo);
      }
    },
    isLoaded: (pageNo: number) => loaded.has(pageNo),
    getActivePage: () => activePage,
    healVisible: () => {
      if (!destroyed) loadVisibleShells();
    },
  };
}
