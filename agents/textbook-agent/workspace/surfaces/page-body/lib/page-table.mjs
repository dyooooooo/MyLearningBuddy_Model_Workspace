import { wrapBareMath } from "./bare-math.mjs";

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function assetUrl(bookId, name) {
  const key = `kb/pdfs/${bookId}/assets/mineru/${name}`;
  return `/api/v1/kb/proxy/asset?key=${encodeURIComponent(key)}`;
}

function rewriteCellImages(html, bookId) {
  if (!bookId) return html;
  return html.replace(
    /(?:src\s*=\s*["'])(?:\.\/)?images\/([^"']+)(["'])/gi,
    (_m, name, quote) => `src=${quote}${assetUrl(bookId, name)}${quote}`,
  );
}

function promoteHeader(html) {
  if (/<th\b/i.test(html)) return html;
  return html.replace(/<tr\b[^>]*>[\s\S]*?<\/tr>/i, (row) =>
    row.replace(/<td\b/gi, "<th").replace(/<\/td>/gi, "</th>"),
  );
}

function columnCount(html) {
  const row = html.match(/<tr\b[^>]*>[\s\S]*?<\/tr>/i);
  if (!row) return 0;
  return (row[0].match(/<t[dh]\b/gi) || []).length;
}

function addTableClass(html, extra) {
  return html.replace(/<table\b([^>]*)>/i, (_m, attrs) => {
    if (/\bclass\s*=/i.test(attrs)) {
      return `<table${attrs.replace(/class\s*=\s*(["'])([^"']*)\1/i, (_c, q, cur) => `class=${q}${cur} ${extra}${q}`)}>`;
    }
    return `<table class="${extra}"${attrs}>`;
  });
}

function polishTable(table, caption, bookId, profile) {
  if (table.includes("kb-table-wrap")) return table;
  let html = rewriteCellImages(table, bookId);
  html = promoteHeader(html);
  html = html.replace(/<(td|th)\b([^>]*)>([\s\S]*?)<\/\1>/gi, (all, tag, attrs, inner) => {
    if (/<img\b/i.test(inner)) return all;
    return `<${tag}${attrs}>${wrapBareMath(inner, profile)}</${tag}>`;
  });
  if (columnCount(html) >= 5) html = addTableClass(html, "kb-table--fit");
  if (caption && !/<caption\b/i.test(html)) {
    html = html.replace(/<table\b([^>]*)>/i, `<table$1><caption>${escapeHtml(caption)}</caption>`);
  }
  return `<div class="kb-table-wrap">${html}</div>`;
}

function isTableShotAlt(alt) {
  const s = (alt || "").trim();
  if (!s) return true;
  if (/^图\s*\d/.test(s)) return false;
  return /^表/.test(s) || s === "插图" || /type['"]\s*:/.test(s);
}

function tableAlreadyWrapped(src, offset) {
  const before = src.slice(0, offset);
  const fence = before.lastIndexOf("::: kb-table-wrap");
  if (fence >= 0 && !before.slice(fence + "::: kb-table-wrap".length).includes("\n:::")) {
    return true;
  }
  const open = before.lastIndexOf('<div class="kb-table-wrap">');
  if (open < 0) return false;
  const afterOpen = before.slice(open);
  const opens = (afterOpen.match(/<div\b/gi) || []).length;
  const closes = (afterOpen.match(/<\/div>/gi) || []).length;
  return opens > closes;
}

export function liftPageTables(md, bookId = "", profile = {}) {
  let text = md || "";
  text = text.replace(
    /(?:^|\n)[ \t]*(?:\*\*)?(表[^\n*]{0,48})(?:\*\*)?[ \t]*\n+(<table\b[\s\S]*?<\/table>)/g,
    (full, cap, table, offset) => {
      const tableAt = offset + full.lastIndexOf(table);
      if (tableAlreadyWrapped(text, tableAt)) return full;
      return `\n${polishTable(table, String(cap).trim(), bookId, profile)}`;
    },
  );
  text = text.replace(/<table\b[\s\S]*?<\/table>/gi, (table, offset, src) => {
    if (table.includes("kb-table-wrap") || tableAlreadyWrapped(src, offset)) return table;
    return polishTable(table, "", bookId, profile);
  });
  text = text.replace(
    /(<div class="kb-table-wrap">[\s\S]*?<\/div>)[ \t]*\n+[ \t]*!\[([^\]]*)\]\([^)]+\)[ \t]*(?:\n+[ \t]*\*[^\n*]+\*[ \t]*)?/g,
    (all, wrap, alt) => (isTableShotAlt(alt) ? wrap : all),
  );
  return text;
}
