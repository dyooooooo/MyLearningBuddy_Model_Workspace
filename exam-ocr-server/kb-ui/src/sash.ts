export type SashOpts = {
  panel: HTMLElement;
  min: number;
  max: () => number;
  defaultWidth: number;
  storageKey: string;
  /** Right-side panel: dragging left grows width. */
  invert?: boolean;
  onWidth: (px: number) => void;
};

export function bindSash(handle: HTMLElement, opts: SashOpts): () => void {
  handle.style.touchAction = "none";

  const apply = (px: number): number => {
    const next = Math.round(Math.min(opts.max(), Math.max(opts.min, px)));
    opts.onWidth(next);
    return next;
  };

  try {
    const saved = Number(window.localStorage.getItem(opts.storageKey) || "");
    if (saved >= opts.min) apply(saved);
  } catch {
    /* ignore */
  }

  const persist = (px: number): void => {
    try {
      window.localStorage.setItem(opts.storageKey, String(px));
    } catch {
      /* ignore */
    }
  };

  const onPointerDown = (ev: PointerEvent): void => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    ev.stopPropagation();
    const startX = ev.clientX;
    const startW = opts.panel.getBoundingClientRect().width || opts.defaultWidth;
    try {
      handle.setPointerCapture(ev.pointerId);
    } catch {
      /* some browsers reject capture on non-connected nodes */
    }
    document.body.classList.add("kb-resizing");

    const onMove = (e: PointerEvent): void => {
      const delta = opts.invert ? startX - e.clientX : e.clientX - startX;
      apply(startW + delta);
    };
    const onUp = (): void => {
      try {
        handle.releasePointerCapture(ev.pointerId);
      } catch {
        /* already released */
      }
      document.body.classList.remove("kb-resizing");
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      document.removeEventListener("pointercancel", onUp);
      persist(opts.panel.getBoundingClientRect().width || startW);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
  };

  const onDblClick = (ev: MouseEvent): void => {
    ev.preventDefault();
    persist(apply(opts.defaultWidth));
  };

  handle.addEventListener("pointerdown", onPointerDown);
  handle.addEventListener("dblclick", onDblClick);
  return () => {
    handle.removeEventListener("pointerdown", onPointerDown);
    handle.removeEventListener("dblclick", onDblClick);
    document.body.classList.remove("kb-resizing");
  };
}
