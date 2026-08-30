function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function resolvePdfPage(entry, printToPdf) {
  const print = entry.print_page != null ? Number(entry.print_page) : null;
  const mapped = print != null ? Number(printToPdf[String(print)] || 0) : 0;
  return Number(entry.pdf_page || mapped || 0);
}

function entryLevel(entry) {
  if (entry.is_chapter) return 0;
  const n = Number(entry.level);
  return Number.isFinite(n) ? n : 1;
}

function tocId(entry, level) {
  return `${level}:${entry.label || ""}:${entry.print_page ?? ""}:${entry.pdf_page ?? ""}`;
}

function buildForest(entries) {
  const root = { children: [], level: -1 };
  const stack = [root];
  for (const entry of entries) {
    const level = entryLevel(entry);
    const node = { entry, level, children: [] };
    while (stack.length > 1 && stack[stack.length - 1].level >= level) {
      stack.pop();
    }
    stack[stack.length - 1].children.push(node);
    stack.push(node);
  }
  return root.children;
}

const CARET =
  '<button type="button" class="kb-toc-caret" aria-expanded="false" aria-label="展开">' +
  '<svg class="kb-chev" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>' +
  "</button>";

const SPACER = '<span class="kb-toc-caret-spacer" aria-hidden="true"></span>';

function nodeHtml(node, printToPdf) {
  const { entry, level, children } = node;
  const hasKids = children.length > 0;
  const pdfPage = resolvePdfPage(entry, printToPdf);
  const rowClasses = [
    "kb-toc-row",
    `kb-toc-row--lv${level}`,
    entry.is_chapter ? "kb-toc-row--chapter" : "",
    pdfPage > 0 ? "kb-toc-row--link" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const attrs = [];
  if (entry.print_page != null) attrs.push(`data-goto-print-page="${Number(entry.print_page)}"`);
  if (pdfPage > 0) {
    attrs.push(`data-goto-page="${pdfPage}"`, 'role="link"', 'tabindex="0"');
  }
  let extra = "";
  if (entry.print_page != null) {
    extra =
      '<span class="kb-toc-leader" aria-hidden="true"></span>' +
      `<span class="kb-toc-page">${escapeHtml(String(entry.print_page))}</span>`;
  }
  const row =
    `<div class="${rowClasses}"${attrs.length ? ` ${attrs.join(" ")}` : ""}>` +
    (hasKids ? CARET : SPACER) +
    `<span class="kb-toc-title">${escapeHtml(entry.label || "")}</span>` +
    extra +
    "</div>";
  const kids = hasKids
    ? `<ol class="kb-toc-list kb-toc-children">${children.map((child) => nodeHtml(child, printToPdf)).join("")}</ol>`
    : "";
  const kind = hasKids ? "kb-toc-node--branch" : "kb-toc-node--leaf";
  const folded = hasKids ? " is-collapsed" : "";
  return `<li class="kb-toc-node ${kind}${folded}" data-toc-id="${escapeHtml(tocId(entry, level))}">${row}${kids}</li>`;
}

export function paint(payload = {}, ctx = {}) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const entries = Array.isArray(payload.entries) ? payload.entries : [];
  const printToPdf = payload.printToPdf && typeof payload.printToPdf === "object" ? payload.printToPdf : {};
  if (!entries.length) {
    return `<p class="muted">${escapeHtml(profile.empty_text || "尚无目录（识别完成后出现）")}</p>`;
  }
  const forest = buildForest(entries);
  return `<ol class="kb-toc-list">${forest.map((node) => nodeHtml(node, printToPdf)).join("")}</ol>`;
}
