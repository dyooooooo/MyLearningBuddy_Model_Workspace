import { fence } from "./lib/fences.mjs";
import { polishPageProse, liftHeadings } from "./lib/page-prose.mjs";
import { liftPageTables } from "./lib/page-table.mjs";
import { looksLikeTocBlob } from "./lib/page-toc.mjs";

const MARKER = /(?<![\w.\-\u4e00-\u9fff])[（(]\s*([0-9０-９]+)\s*[）)]/g;
const LEAD = /^((?:[\u4e00-\u9fff]{1,6})?定理|定义|证明|探究与实践)(?:\s+|$)/;
const SOLUTION = /^解(?:[\s\u3000]+|(?=[（(]))(.*)$/;
const IMG_BLOCK =
  /!\[([^\]]*)\]\(([^)]+)\)(?:\s*\n+\s*\*([^*\n]+)\*)?/g;

function toAsciiDigit(s) {
  return s.replace(/[０-９]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) - 0xff10 + 48));
}

function unifyMarker(text) {
  return text.replace(/^[（(]\s*([0-9０-９]+)\s*[）)]\s*/, (_m, n) => `（${toAsciiDigit(n)}）`);
}

function markerMatches(text) {
  return [...text.matchAll(new RegExp(MARKER.source, "g"))];
}

function hasPackedMarkers(text) {
  const matches = markerMatches(text);
  for (let i = 0; i < matches.length - 1; i++) {
    const aEnd = (matches[i].index ?? 0) + matches[i][0].length;
    const bStart = matches[i + 1].index ?? 0;
    if (!text.slice(aEnd, bStart).trim()) return true;
  }
  return false;
}

function splitNumbered(text) {
  const matches = markerMatches(text);
  if (matches.length < 2 || hasPackedMarkers(text)) return null;
  const chunks = [];
  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index ?? 0;
    const end = i + 1 < matches.length ? (matches[i + 1].index ?? text.length) : text.length;
    chunks.push(text.slice(start, end).trim());
  }
  const markerOnly = /(?<![\w.\-\u4e00-\u9fff])[（(]\s*[0-9０-９]+\s*[）)]/g;
  const rich = chunks.filter((c) => c.replace(markerOnly, "").trim().length >= 4);
  if (rich.length < 2) return null;
  const prefix = text.slice(0, matches[0].index ?? 0).trim();
  return prefix && prefix !== "解" ? [prefix, ...chunks] : chunks;
}

function stripSkin(md) {
  let text = md || "";
  text = text.replace(
    /<p class="kb-list-item">([\s\S]*?)<\/p>/gi,
    (_m, inner) => inner.replace(/<[^>]+>/g, "").trim(),
  );
  text = text.replace(
    /<p class="kb-solution-tail">([\s\S]*?)<\/p>/gi,
    (_m, inner) => inner.replace(/<[^>]+>/g, "").trim(),
  );
  text = text.replace(/<p class="kb-solution">[\s\S]*?<\/p>/gi, "解");
  text = text.replace(
    /<p class="kb-lead">[\s\S]*?<span class="kb-lead-label">([\s\S]*?)<\/span>([\s\S]*?)<\/p>/gi,
    (_m, label, rest) => `${label.replace(/<[^>]+>/g, "").trim()} ${rest.replace(/<[^>]+>/g, "").trim()}`.trim(),
  );
  text = text.replace(
    /<(?:div|p|h[1-6]|span) class="kb-page-toc-heading">([\s\S]*?)<\/(?:div|p|h[1-6]|span)>/gi,
    (_m, inner) => `${inner.replace(/<[^>]+>/g, "").trim()}\n`,
  );
  const unwrapToc = (nav) => {
    const rows = [];
    const re = /<li\b[^>]*>([\s\S]*?)<\/li>/gi;
    let row;
    while ((row = re.exec(nav))) {
      const title = (row[1].match(/kb-page-toc-title">([\s\S]*?)<\//) || [])[1] || "";
      const page = (row[1].match(/kb-page-toc-page">([\s\S]*?)<\//) || [])[1] || "";
      const label = title.replace(/<[^>]+>/g, "").trim();
      const num = page.replace(/<[^>]+>/g, "").trim();
      if (label) rows.push(num ? `${label} ${num}` : label);
    }
    return `\n${rows.join("\n")}`;
  };
  text = text.replace(/<nav class="kb-page-toc"[\s\S]*?<\/nav>/gi, unwrapToc);
  text = text.replace(/<ol\b[^>]*class="[^"]*kb-page-toc[\s\S]*?<\/ol>/gi, unwrapToc);
  text = text.replace(/<div class="kb-center">\s*([\s\S]*?)\s*<\/div>/gi, "$1");
  text = text.replace(/<div class="kb-table-wrap">([\s\S]*?)<\/div>/gi, "$1");
  text = text.replace(/::: kb-[\w-]+\r?\n([\s\S]*?)\r?\n:::/g, "$1");
  text = text.replace(/^(目录|目 录|CONTENTS|Contents)(?=\S)/gm, "$1\n");
  return text;
}

function tagListsAndLeads(md) {
  const out = [];
  for (const raw of (md || "").split("\n")) {
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith("<") || trimmed.startsWith("!") || trimmed.startsWith("#") || trimmed.startsWith(":::")) {
      out.push(raw);
      continue;
    }
    let body = trimmed;
    let solution = false;
    const sol = SOLUTION.exec(body);
    if (sol && !body.startsWith("解题")) {
      solution = true;
      body = sol[1].trim();
    }
    const chunks = splitNumbered(body);
    if (solution) out.push(fence("kb-solution", "解"));
    if (chunks) {
      for (const chunk of chunks) out.push(fence("kb-list-item", unifyMarker(chunk)));
      continue;
    }
    if (/^[（(]\s*[0-9０-９]+\s*[）)]/.test(body) && !hasPackedMarkers(body)) {
      out.push(fence("kb-list-item", unifyMarker(body)));
      continue;
    }
    if (/^\d+\.\s+[\u4e00-\u9fff]/.test(body) && !/^\d+\.\d+/.test(body)) {
      out.push(fence("kb-list-item", body));
      continue;
    }
    if (solution && body) {
      out.push(fence("kb-solution-tail", body));
      continue;
    }
    const lead = LEAD.exec(trimmed);
    if (lead && !trimmed.startsWith("#")) {
      out.push(fence("kb-lead", trimmed));
      continue;
    }
    out.push(raw);
  }
  return out.join("\n");
}

function tagToc(md, profile) {
  const raw = md || "";
  if (raw.includes("::: kb-page-toc") || raw.includes('class="kb-page-toc"')) return raw;
  if (!looksLikeTocBlob(raw, profile)) return raw;
  return fence("kb-page-toc", raw.trim());
}

function tagCenters(md, centerTexts) {
  let text = md || "";
  const wanted = new Set(
    (Array.isArray(centerTexts) ? centerTexts : [])
      .map((t) => String(t || "").trim())
      .filter(Boolean),
  );
  text = text.replace(IMG_BLOCK, (all) => {
    if (all.includes("::: kb-center")) return all;
    return fence("kb-center", all.trim());
  });
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (all) => {
    if (all.includes("::: kb-center")) return all;
    return fence("kb-center", all.trim());
  });
  if (!wanted.size) return text;
  return text
    .split("\n")
    .map((line) => {
      const t = line.trim();
      if (!t || t.startsWith(":::") || t.startsWith("<") || t.startsWith("!")) return line;
      if (wanted.has(t) || [...wanted].some((w) => t.includes(w) && w.length >= 4)) {
        return fence("kb-center", t);
      }
      return line;
    })
    .join("\n");
}

export function tag(md, ctx = {}) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const bookId = String(ctx.bookId || "");
  const cleaned = polishPageProse(stripSkin(md || ""));
  const plain = (cleaned || "").replace(/^#{1,6}\s+/gm, "");
  if (looksLikeTocBlob(plain, profile)) {
    return tagToc(plain, profile);
  }
  const structured = liftPageTables(liftHeadings(cleaned), bookId, profile).replace(
    /<div class="kb-table-wrap">([\s\S]*?)<\/div>/gi,
    (_m, inner) => fence("kb-table-wrap", String(inner || "").trim()),
  );
  const listed = tagListsAndLeads(structured);
  return tagCenters(listed, ctx.centerTexts);
}
