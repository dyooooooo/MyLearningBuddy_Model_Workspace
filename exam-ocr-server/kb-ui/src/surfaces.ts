import { json, request } from "./api";
import { sanitizeHtml } from "./util/html-sanitize";

export type SurfaceSlot = {
  slot: string;
  loaded: boolean;
  source: string;
  profile: Record<string, unknown>;
  css: string;
};

const cache = new Map<string, SurfaceSlot>();
let generation = 0;

export function setSurfaceGeneration(n: number): void {
  if (Number.isFinite(n) && n > 0) generation = n;
}

export function invalidateSurface(slot?: string): void {
  if (!slot) cache.clear();
  else cache.delete(slot);
}

export async function loadSurface(slot: string): Promise<SurfaceSlot> {
  const hit = cache.get(slot);
  if (hit) return hit;
  const meta = await json<SurfaceSlot>(`/api/v1/kb/agent/surfaces/${encodeURIComponent(slot)}`);
  let css = String(meta.css || "");
  if (!css) {
    const q = generation ? `?g=${encodeURIComponent(String(generation))}` : "";
    const r = await request(`/api/v1/kb/agent/surfaces/${encodeURIComponent(slot)}/theme.css${q}`);
    css = r.ok ? await r.text() : "";
  }
  const row: SurfaceSlot = {
    slot,
    loaded: Boolean(meta.loaded),
    source: String(meta.source || ""),
    profile: meta.profile && typeof meta.profile === "object" ? meta.profile : {},
    css,
  };
  cache.set(slot, row);
  return row;
}

export function applyDocumentTheme(slot: string, css: string): void {
  const id = `kb-surface-style-${slot}`;
  let node = document.getElementById(id) as HTMLStyleElement | null;
  if (!node) {
    node = document.createElement("style");
    node.id = id;
    document.head.appendChild(node);
  }
  node.textContent = css || "";
}

export async function fillShadow(host: HTMLElement, slot: string, html: string): Promise<void> {
  const { css } = await loadSurface(slot);
  const root = host.shadowRoot || host.attachShadow({ mode: "open" });
  root.innerHTML = `<style>${css}</style><div class="kb-surface-inner">${sanitizeHtml(html)}</div>`;
}

export function setShadowText(host: HTMLElement, text: string): void {
  const inner = host.shadowRoot?.querySelector(".kb-surface-inner");
  if (inner) {
    inner.textContent = text;
    return;
  }
  host.textContent = text;
}
