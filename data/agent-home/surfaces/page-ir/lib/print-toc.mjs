// 印刷目录页：按 ir/print_toc.json 的行画成目录列表。
// 结构与样式照老线 page-body 的 kb-page-toc（不引用 page-body），类名用 kb-ir-toc。

const LEVEL_MAX = 4;

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pageNumber(value) {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : null;
}

export function printTocPage(printToc, pageNo) {
  const pages = printToc && typeof printToc === "object" ? printToc.pages : null;
  const n = pageNumber(pageNo);
  if (!pages || typeof pages !== "object" || n == null) return null;
  const item = pages[String(n)];
  return item && Array.isArray(item.rows) && item.rows.length ? item : null;
}

function rowHtml(row) {
  const raw = Number(row?.level);
  const level = Number.isInteger(raw) ? Math.max(0, Math.min(LEVEL_MAX, raw)) : 0;
  const classes = ["kb-ir-toc-row", `kb-ir-toc-row--lv${level}`];
  if (row?.chapter) classes.push("kb-ir-toc-row--chapter");
  const attrs = [];
  let tail = "";
  const printed = pageNumber(row?.print_page);
  if (printed != null) {
    classes.push("kb-ir-toc-row--link");
    attrs.push(`data-goto-print-page="${printed}"`);
    const goto = pageNumber(row?.goto_page);
    if (goto != null) attrs.push(`data-goto-page="${goto}"`);
    attrs.push('role="link"', 'tabindex="0"');
    tail = `<span class="kb-ir-toc-leader" aria-hidden="true"></span><span class="kb-ir-toc-page">${printed}</span>`;
  }
  if (row?.needs_review) attrs.push('data-needs-review="1"');
  return (
    `<li class="${classes.join(" ")}"${attrs.length ? ` ${attrs.join(" ")}` : ""}>` +
    `<span class="kb-ir-toc-title">${escapeHtml(row?.label)}</span>${tail}</li>`
  );
}

export function renderPrintTocPage(item) {
  const heading = item?.heading ? `<div class="kb-ir-toc-heading">${escapeHtml(item.heading)}</div>` : "";
  const rows = (item?.rows || []).map(rowHtml).join("");
  return (
    `<div class="kb-ir-page kb-ir-page--toc">${heading}` +
    `<nav class="kb-ir-toc" data-toc-source="print"><ol class="kb-ir-toc-list">${rows}</ol></nav></div>`
  );
}
