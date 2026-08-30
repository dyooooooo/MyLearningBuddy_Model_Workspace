import { el } from "./util/dom";

let open: { root: HTMLElement; onClose: () => void } | null = null;

export function closePopover(): void {
  if (!open) return;
  open.root.remove();
  open.onClose();
  open = null;
}

export function openPopover(
  anchor: HTMLElement,
  build: (close: () => void) => HTMLElement,
  opts: { align?: "left" | "right"; width?: number; onClose?: () => void } = {},
): void {
  closePopover();
  const root = el("div", "kb-pop");
  if (opts.width) root.style.minWidth = `${opts.width}px`;
  const close = () => closePopover();
  root.append(build(close));
  document.body.append(root);
  const a = anchor.getBoundingClientRect();
  const w = opts.width || Math.max(220, a.width);
  placePopover(root, a, w, opts.align || "left");

  const onDoc = (ev: Event) => {
    const t = ev.target as Node | null;
    if (t && (root.contains(t) || anchor.contains(t))) return;
    close();
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") close();
  };
  const openedAt = Date.now();
  const onScroll = (ev: Event) => {
    if (Date.now() - openedAt < 200) return;
    const t = ev.target;
    if (!(t instanceof Node)) return;
    if (root.contains(t)) return;
    if (t instanceof Element && t.closest(".kb-dock-stream, .kb-dock-body, .kb-pop")) return;
    close();
  };
  window.setTimeout(() => {
    document.addEventListener("pointerdown", onDoc, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", onScroll, true);
  }, 0);
  open = {
    root,
    onClose: () => {
      document.removeEventListener("pointerdown", onDoc, true);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", onScroll, true);
      opts.onClose?.();
    },
  };
}

function placePopover(
  root: HTMLElement,
  a: DOMRect,
  w: number,
  align: "left" | "right",
): void {
  let left = align === "right" ? a.right - w : a.left;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  const gap = 6;
  const pad = 8;
  const spaceBelow = window.innerHeight - a.bottom - gap - pad;
  const spaceAbove = a.top - gap - pad;
  const want = Math.min(22 * 16, Math.floor(window.innerHeight * 0.7));
  const openUp = spaceBelow < Math.min(want, 200) && spaceAbove > spaceBelow;
  root.style.left = `${left}px`;
  root.style.width = `${w}px`;
  if (openUp) {
    root.style.top = "auto";
    root.style.bottom = `${window.innerHeight - a.top + gap}px`;
    root.style.maxHeight = `${Math.max(96, Math.min(want, spaceAbove))}px`;
  } else {
    root.style.bottom = "auto";
    root.style.top = `${a.bottom + gap}px`;
    root.style.maxHeight = `${Math.max(96, Math.min(want, Math.max(spaceBelow, 160)))}px`;
  }
}

export type ChevronDir = "down" | "up" | "left" | "right";

/** Stroke chevron, 16px — same scale as plus / paperclip / copy. */
export function chevron(dir: ChevronDir = "down", size = 16): string {
  return `<svg class="kb-chev kb-chev--${dir}" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>`;
}
