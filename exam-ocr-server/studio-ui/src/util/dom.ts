import { marked } from "marked";
import { sanitizeHtml } from "./html-sanitize";
import { preprocessMarkdownMath, restoreMathPlaceholders } from "./math-render";

marked.setOptions({ gfm: true, breaks: false });

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function renderMarkdown(md: string): string {
  let text = md || "";
  text = text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, id, label) => {
    const display = label || id;
    return `<a href="#" class="wikilink" data-node-id="${escapeAttr(id)}">${escapeAttr(display)}</a>`;
  });
  const { text: withPlaceholders, blocks } = preprocessMarkdownMath(text);
  const html = marked.parse(withPlaceholders, { async: false }) as string;
  return sanitizeHtml(restoreMathPlaceholders(html, blocks));
}

export function statusClass(status?: string): string {
  return `status-${status || "draft"}`;
}

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export function debounce<T extends (...args: unknown[]) => void>(fn: T, ms: number): T {
  let t: ReturnType<typeof setTimeout>;
  return ((...args: unknown[]) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  }) as T;
}
