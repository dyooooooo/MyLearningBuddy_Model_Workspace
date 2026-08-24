const DANGEROUS_TAGS = ["script", "style", "iframe", "object", "embed", "link", "meta", "base", "form"];

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
