// Sanitize server-rendered textbook layout HTML before injecting via innerHTML.
//
// Blocklist approach: drop executable/embedding elements, strip inline event
// handlers and javascript: URLs, but preserve layout structure, class names,
// data-* attributes (MathJax spans, block ids) and image sources so the
// textbook stylesheet renders faithfully.

export const TEXTBOOK_CSS_HREF = "/api/v1/studio/proxy/import/textbook.css";
/** Bump to force browsers to drop a stale textbook stylesheet. */
export const TEXTBOOK_CSS_VERSION = "20260728-fig-row-align";

const DANGEROUS_TAGS = ["script", "style", "iframe", "object", "embed", "link", "meta", "base", "form"];

/**
 * Heuristic: does this stored content carry server-rendered HTML layout
 * (as opposed to Markdown / plain prose)?
 */
export function isHtmlContent(text: string, meta?: Record<string, unknown>): boolean {
  const fmt = String((meta && (meta.render_format || meta.content_format)) || "").toLowerCase();
  if (fmt === "html" || fmt === "layout_html") return true;
  if (meta && (meta.layout_html || meta.html)) return true;
  const t = text || "";
  // Layout HTML from the corpus renderer uses lb-* block wrappers / data-block.
  if (/class="lb-|data-block|data-latex/i.test(t)) return true;
  return /<(div|section|table|figure|p|h[1-6]|ul|ol)[\s>]/i.test(t);
}

export function sanitizeHtml(html: string): string {
  if (!html) return "";
  let doc: Document;
  try {
    doc = new DOMParser().parseFromString(html, "text/html");
  } catch {
    return "";
  }
  const root = doc.body;
  if (!root) return "";

  root.querySelectorAll(DANGEROUS_TAGS.join(",")).forEach((n) => n.remove());

  root.querySelectorAll("*").forEach((elm) => {
    for (const attr of Array.from(elm.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value || "";
      if (name.startsWith("on")) {
        elm.removeAttribute(attr.name);
        continue;
      }
      if ((name === "href" || name === "src" || name === "xlink:href") && /^\s*javascript:/i.test(value)) {
        elm.removeAttribute(attr.name);
      }
    }
  });

  return root.innerHTML;
}
