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
  opts: { align?: "left" | "right"; width?: number } = {},
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
  window.setTimeout(() => {
    document.addEventListener("pointerdown", onDoc, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
  }, 0);
  open = {
    root,
    onClose: () => {
      document.removeEventListener("pointerdown", onDoc, true);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
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

export function chevron(): string {
  return `<svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path fill="currentColor" d="M2.4 4.2 6 7.8l3.6-3.6z"/></svg>`;
}
