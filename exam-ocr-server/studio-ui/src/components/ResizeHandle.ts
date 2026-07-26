export type ResizeEdge = "left" | "right";

export interface ResizeHandleOptions {
  panel: HTMLElement;
  edge: ResizeEdge;
  storageKey: string;
  cssVar: string;
  minWidth?: number;
  maxWidth?: number;
  /** Cap as fraction of viewport width (default 0.45). */
  maxWidthRatio?: number;
}

export interface ResizeHandleControl {
  el: HTMLElement;
  setLimits: (opts: {
    minWidth?: number;
    maxWidth?: number;
    maxWidthRatio?: number;
    storageKey?: string;
    /** Used when storage has no saved width (and when reloadStored). */
    fallbackWidth?: number;
    /** When true, load width from (new) storageKey instead of keeping current. */
    reloadStored?: boolean;
    /** Apply this width now (clamped); skips storage load. */
    forceWidth?: number;
  }) => void;
  reclamp: () => void;
}

function clampWidth(
  w: number,
  minWidth: number,
  maxWidth: number,
  maxWidthRatio: number,
): number {
  const max = Math.min(maxWidth, Math.floor(window.innerWidth * maxWidthRatio));
  return Math.max(minWidth, Math.min(max, w));
}

function loadWidth(
  storageKey: string,
  fallback: number,
  minWidth: number,
  maxWidth: number,
  maxWidthRatio: number,
): number {
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw) {
      const n = parseInt(raw, 10);
      if (!Number.isNaN(n)) return clampWidth(n, minWidth, maxWidth, maxWidthRatio);
    }
  } catch {
    /* ignore */
  }
  return clampWidth(fallback, minWidth, maxWidth, maxWidthRatio);
}

function applyWidth(
  panel: HTMLElement,
  width: number,
  cssVar: string,
  minWidth: number,
  maxWidth: number,
  maxWidthRatio: number,
): void {
  const w = clampWidth(width, minWidth, maxWidth, maxWidthRatio);
  panel.style.width = `${w}px`;
  document.documentElement.style.setProperty(cssVar, `${w}px`);
}

export function initPanelWidth(
  opts: Pick<
    ResizeHandleOptions,
    "panel" | "storageKey" | "cssVar" | "minWidth" | "maxWidth" | "maxWidthRatio"
  > & { fallback?: number },
): void {
  const minWidth = opts.minWidth ?? 200;
  const maxWidth = opts.maxWidth ?? 480;
  const maxWidthRatio = opts.maxWidthRatio ?? 0.45;
  const fallback = opts.fallback ?? 280;
  applyWidth(
    opts.panel,
    loadWidth(opts.storageKey, fallback, minWidth, maxWidth, maxWidthRatio),
    opts.cssVar,
    minWidth,
    maxWidth,
    maxWidthRatio,
  );
}

export function mountResizeHandle(opts: ResizeHandleOptions): ResizeHandleControl {
  let minWidth = opts.minWidth ?? 200;
  let maxWidth = opts.maxWidth ?? 480;
  let maxWidthRatio = opts.maxWidthRatio ?? 0.45;
  let storageKey = opts.storageKey;
  const fallback = opts.edge === "left" ? 300 : 280;

  initPanelWidth({ ...opts, fallback, minWidth, maxWidth, maxWidthRatio });

  const handle = document.createElement("div");
  handle.className = "studio-resize-handle";
  handle.setAttribute("role", "separator");
  handle.setAttribute("aria-orientation", "vertical");
  handle.setAttribute(
    "aria-label",
    opts.edge === "left" ? "调整右侧面板宽度" : "调整左侧面板宽度",
  );

  let dragging = false;
  let startX = 0;
  let startW = 0;

  const onMove = (e: MouseEvent): void => {
    if (!dragging) return;
    const delta = opts.edge === "right" ? e.clientX - startX : startX - e.clientX;
    applyWidth(opts.panel, startW + delta, opts.cssVar, minWidth, maxWidth, maxWidthRatio);
  };

  const onUp = (): void => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("studio-resizing");
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    try {
      localStorage.setItem(storageKey, String(opts.panel.offsetWidth));
    } catch {
      /* ignore */
    }
  };

  handle.addEventListener("mousedown", (e) => {
    e.preventDefault();
    dragging = true;
    startX = e.clientX;
    startW = opts.panel.offsetWidth;
    document.body.classList.add("studio-resizing");
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });

  const reclamp = (): void => {
    applyWidth(
      opts.panel,
      opts.panel.offsetWidth || fallback,
      opts.cssVar,
      minWidth,
      maxWidth,
      maxWidthRatio,
    );
  };

  const setLimits: ResizeHandleControl["setLimits"] = (next) => {
    if (next.minWidth != null) minWidth = next.minWidth;
    if (next.maxWidth != null) maxWidth = next.maxWidth;
    if (next.maxWidthRatio != null) maxWidthRatio = next.maxWidthRatio;
    if (next.storageKey) storageKey = next.storageKey;
    const fb = next.fallbackWidth ?? (opts.panel.offsetWidth || fallback);
    if (next.forceWidth != null) {
      applyWidth(opts.panel, next.forceWidth, opts.cssVar, minWidth, maxWidth, maxWidthRatio);
      try {
        localStorage.setItem(storageKey, String(opts.panel.offsetWidth));
      } catch {
        /* ignore */
      }
    } else if (next.reloadStored) {
      applyWidth(
        opts.panel,
        loadWidth(storageKey, fb, minWidth, maxWidth, maxWidthRatio),
        opts.cssVar,
        minWidth,
        maxWidth,
        maxWidthRatio,
      );
    } else {
      reclamp();
    }
  };

  return { el: handle, setLimits, reclamp };
}

/** @deprecated use mountResizeHandle with options */
export function initSidePanelWidth(panel: HTMLElement): void {
  initPanelWidth({
    panel,
    storageKey: "studio-sidebar-w",
    cssVar: "--sidebar-w",
  });
}
