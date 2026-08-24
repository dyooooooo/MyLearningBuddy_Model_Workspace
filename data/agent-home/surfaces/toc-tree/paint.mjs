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

function rowHtml(entry, printToPdf) {
  const pdfPage = resolvePdfPage(entry, printToPdf);
  const level = entry.is_chapter ? 0 : Number(entry.level || 1);
  const classes = [
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
  return (
    `<li class="${classes}"${attrs.length ? ` ${attrs.join(" ")}` : ""}>` +
    `<span class="kb-toc-title">${escapeHtml(entry.label || "")}</span>` +
    `${extra}</li>`
  );
}

export function paint(payload = {}, ctx = {}) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const entries = Array.isArray(payload.entries) ? payload.entries : [];
  const printToPdf = payload.printToPdf && typeof payload.printToPdf === "object" ? payload.printToPdf : {};
  if (!entries.length) {
    return `<p class="muted">${escapeHtml(profile.empty_text || "尚无目录（识别完成后出现）")}</p>`;
  }
  return `<ol class="kb-toc-list">${entries.map((entry) => rowHtml(entry, printToPdf)).join("")}</ol>`;
}
