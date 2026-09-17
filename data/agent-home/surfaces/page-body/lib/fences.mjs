import { wrapBareMath } from "./bare-math.mjs";
import { stripBlockIds } from "./block-ids.mjs";
import { extractOfm, extractTopLevel } from "./md-parse.bundle.mjs";
import { liftPageToc } from "./page-toc.mjs";

export const FENCE_NAMES = new Set([
  "kb-center",
  "kb-title",
  "kb-subtitle",
  "kb-author",
  "kb-section",
  "kb-section-center",
  "kb-verse",
  "kb-verse-center",
  "kb-date",
  "kb-list-item",
  "kb-lead",
  "kb-solution",
  "kb-solution-tail",
  "kb-page-toc",
  "kb-table-wrap",
  "kb-section-icon",
  "kb-drama",
  "kb-stage",
  "kb-dash",
]);

const FENCE = /::: (kb-[\w-]+)\r?\n([\s\S]*?)\r?\n:::/g;
const LEAD = /^((?:[\u4e00-\u9fff]{1,6})?定理|定义|证明|探究与实践)(?:\s+|$)/;
const ICON_IMG = /!\[([^\]]*)\]\(([^)]+)\)/;
const ICON_HEAD = /^(#{1,6})\s+(.+?)\s*$/;
const SPOKEN_SPACE = /^([\u4e00-\u9fffA-Za-z·．.]{2,8})[ \t\u3000]+(（[^）]*）)?[ \t\u3000]*(.*)$/;
const SPOKEN_PAREN = /^([\u4e00-\u9fffA-Za-z·．.]{2,8})(（[^）]*）)(.*)$/;
const ARIA = /^(【[^】]+】)(.*)$/;

export function hasKbFences(md) {
  return /::: kb-[\w-]+/.test(md || "");
}

export function fence(name, body) {
  const inner = String(body ?? "").replace(/\s+$/, "");
  return `::: ${name}\n${inner}\n:::`;
}

function escapeText(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  return escapeText(String(s || "")).replace(/"/g, "&quot;");
}

function itemHtml(text, cls, profile) {
  return `<p class="${cls}">${wrapBareMath(escapeText(text), profile)}</p>`;
}

function paintInline(text, profile) {
  return wrapBareMath(escapeText(text || ""), profile);
}

function paintLead(body, profile) {
  const trimmed = (body || "").trim();
  const match = LEAD.exec(trimmed);
  if (!match) {
    return `<p class="kb-lead">${wrapBareMath(escapeText(trimmed), profile)}</p>`;
  }
  const rest = trimmed.slice(match[0].length).trim();
  const label = match[1];
  if (rest) {
    return (
      `<p class="kb-lead"><span class="kb-lead-label">${escapeText(label)}</span> ` +
      `${wrapBareMath(escapeText(rest), profile)}</p>`
    );
  }
  return `<p class="kb-lead"><span class="kb-lead-label">${escapeText(label)}</span></p>`;
}

function paintSectionIcon(body) {
  const lines = String(body || "")
    .split(/\r?\n/)
    .map((ln) => ln.trim())
    .filter(Boolean);
  let alt = "插图";
  let src = "";
  let label = "";
  for (const line of lines) {
    const img = ICON_IMG.exec(line);
    if (img && !src) {
      alt = img[1] || "插图";
      src = img[2];
      continue;
    }
    const head = ICON_HEAD.exec(line);
    if (head && !label) {
      label = head[2];
      continue;
    }
    if (!label && !line.startsWith("!")) {
      label = line.replace(/^#{1,6}\s+/, "");
    }
  }
  if (!src || !label) return body;
  return (
    `<div class="kb-section-icon">` +
    `<img src="${escapeAttr(src)}" alt="${escapeAttr(alt)}">` +
    `<span class="kb-section-icon-label">${escapeText(label)}</span>` +
    `</div>\n\n`
  );
}

function paintSpeech(role, dir, line, profile) {
  const dirHtml = dir
    ? `<span class="kb-speech-dir">${paintInline(dir, profile)}</span>`
    : "";
  const lineHtml = line
    ? `<span class="kb-speech-line">${paintInline(line, profile)}</span>`
    : "";
  const gap = dirHtml && lineHtml ? " " : "";
  return (
    `<p class="kb-speech"><span class="kb-speech-role">${paintInline(role, profile)}</span>` +
    `<span class="kb-speech-body">${dirHtml}${gap}${lineHtml}</span></p>`
  );
}

function paintYuan(text, profile) {
  const parts = [];
  const token = /（[^）]+）/g;
  let last = 0;
  let match = token.exec(text);
  while (match) {
    if (match.index > last) {
      parts.push(paintInline(text.slice(last, match.index), profile));
    }
    parts.push(`<span class="kb-speech-dir">${paintInline(match[0], profile)}</span>`);
    last = match.index + match[0].length;
    match = token.exec(text);
  }
  if (last < text.length) parts.push(paintInline(text.slice(last), profile));
  return `<p class="kb-drama-yuan">${parts.join("")}</p>`;
}

function paintAria(title, line, profile) {
  return (
    `<p class="kb-drama-aria"><span class="kb-speech-aria">${paintInline(title, profile)}</span>` +
    `<span class="kb-speech-line">${paintInline((line || "").trim(), profile)}</span></p>`
  );
}

function paintDrama(body, profile) {
  const parts = String(body || "").split(/\n\s*\n/);
  const rows = [];
  for (const raw of parts) {
    const text = raw.replace(/\s*\n\s*/g, "").trim();
    if (!text) continue;
    const aria = ARIA.exec(text);
    if (aria) {
      rows.push(paintAria(aria[1], aria[2], profile));
      continue;
    }
    const spaced = SPOKEN_SPACE.exec(text);
    if (spaced && spaced[3]) {
      rows.push(paintSpeech(spaced[1], spaced[2] || "", spaced[3], profile));
      continue;
    }
    const glued = SPOKEN_PAREN.exec(text);
    if (glued) {
      rows.push(paintSpeech(glued[1], glued[2], glued[3], profile));
      continue;
    }
    if (text.startsWith("（") && /云|唱|科/.test(text.slice(0, 48))) {
      rows.push(paintYuan(text, profile));
      continue;
    }
    rows.push(`<p class="kb-drama-note">${paintInline(text, profile)}</p>`);
  }
  return `<div class="kb-drama">\n${rows.join("\n")}\n</div>`;
}

function peelInnerFenceTokens(body) {
  const names =
    "kb-(?:center|title|subtitle|author|section(?:-center)?|verse(?:-center)?|" +
    "date|list-item|lead|solution(?:-tail)?|page-toc|table-wrap|section-icon|drama|stage|dash)";
  const nameLine = new RegExp(`^[ \\t]*(?::: )?(?:${names})[ \\t]*$`);
  const nameTok = new RegExp(`(?:^|(?<=\\s))(?:${names})(?=\\s|[^\\w-]|$)`);
  const closer = /^[ \t]*:::[ \t]*$/;
  const lines = [];
  for (const line of String(body || "").split(/\r?\n/)) {
    if (closer.test(line) || nameLine.test(line)) continue;
    const cleaned = line
      .replace(/::: kb-[\w-]+/g, "")
      .replace(new RegExp(nameTok.source, "g"), "")
      .replace(/[ \t]{2,}/g, " ")
      .replace(/[ \t]+$/, "");
    if (!cleaned.trim() && (/::: kb-[\w-]+/.test(line) || nameTok.test(line))) continue;
    lines.push(cleaned);
  }
  return lines.join("\n");
}

function parserMode(ctx) {
  const raw = ctx?.profile && typeof ctx.profile === "object" ? ctx.profile.parser : "";
  return String(raw || "remark").trim().toLowerCase();
}

function renderWikilink(hit) {
  const path = String(hit.url || "").trim();
  const label = String(hit.alias || path || hit.value || "").trim() || path;
  const frag = String(hit.fragment || "").replace(/^#/, "");
  const href = `#ofm-${encodeURIComponent(path)}${frag ? `#${encodeURIComponent(frag)}` : ""}`;
  return `<a class="kb-wikilink" href="${escapeAttr(href)}">${escapeText(label)}</a>`;
}

function renderCallout(hit) {
  const lines = String(hit.value || "").split(/\r?\n/);
  const title = String(hit.title || hit.calloutType || "").trim();
  const body = lines
    .slice(1)
    .map((line) => line.replace(/^>\s?/, ""))
    .join("\n")
    .trim();
  const kind = escapeAttr(hit.calloutType || "note");
  return (
    `<aside class="kb-ofm-callout" data-callout="${kind}">` +
    (title ? `<p class="kb-ofm-callout-title">${escapeText(title)}</p>` : "") +
    (body ? `<p class="kb-ofm-callout-body">${escapeText(body)}</p>` : "") +
    `</aside>`
  );
}

function renderOfmHit(hit) {
  if (hit.kind === "wikilink") return renderWikilink(hit);
  if (hit.kind === "highlight") {
    const inner = String(hit.value || "").replace(/^==/, "").replace(/==$/, "");
    return `<mark class="kb-ofm-highlight">${escapeText(inner)}</mark>`;
  }
  if (hit.kind === "comment") return "";
  if (hit.kind === "callout") return renderCallout(hit);
  return hit.value || "";
}

export function paintOfm(text, { escape = false } = {}) {
  const raw = String(text || "");
  const hits = extractOfm(raw);
  if (!hits.length) return escape ? escapeText(raw) : raw;
  let out = "";
  let cursor = 0;
  for (const hit of hits) {
    const gap = raw.slice(cursor, hit.start);
    out += escape ? escapeText(gap) : gap;
    out += renderOfmHit(hit);
    cursor = hit.end;
  }
  const tail = raw.slice(cursor);
  out += escape ? escapeText(tail) : tail;
  return out;
}

function paintOne(name, rawInner, ctx) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const printToPdf = ctx.printToPdf && typeof ctx.printToPdf === "object" ? ctx.printToPdf : {};
  const kids = extractTopLevel(rawInner);
  let body;
  if (kids.length) {
    body = stripBlockIds(paintFences(rawInner, ctx));
  } else {
    body = stripBlockIds(
      peelInnerFenceTokens(String(rawInner || "").replace(/^\n/, "").replace(/\n$/, "")),
    );
    if (
      name !== "kb-list-item" &&
      name !== "kb-solution-tail" &&
      name !== "kb-lead" &&
      name !== "kb-drama"
    ) {
      body = paintOfm(body, { escape: false });
    }
  }
  const tocEntries = Array.isArray(ctx.tocEntries) ? ctx.tocEntries : [];
  if (name === "kb-page-toc") return liftPageToc(body, printToPdf, profile, tocEntries, ctx.lexicon);
  if (name === "kb-list-item") return itemHtml(body, "kb-list-item", profile);
  if (name === "kb-solution") {
    return '<p class="kb-solution"><span class="kb-solution-label">解</span></p>';
  }
  if (name === "kb-solution-tail") return itemHtml(body, "kb-solution-tail", profile);
  if (name === "kb-lead") return paintLead(body, profile);
  if (name === "kb-center") return `<div class="kb-center">\n\n${body}\n\n</div>`;
  if (name === "kb-title") return `<div class="kb-title">\n\n${body}\n\n</div>`;
  if (name === "kb-subtitle") return `<div class="kb-subtitle">\n\n${body}\n\n</div>`;
  if (name === "kb-author") return `<div class="kb-author">\n\n${body}\n\n</div>`;
  if (name === "kb-section") return `<div class="kb-section">\n\n${body}\n\n</div>`;
  if (name === "kb-section-center") return `<div class="kb-section-center">\n\n${body}\n\n</div>`;
  if (name === "kb-verse") return `<div class="kb-verse">\n\n${body}\n\n</div>`;
  if (name === "kb-verse-center") return `<div class="kb-verse-center">\n\n${body}\n\n</div>`;
  if (name === "kb-date") return `<div class="kb-date">\n\n${body}\n\n</div>`;
  if (name === "kb-table-wrap") return `<div class="kb-table-wrap">${body}</div>`;
  if (name === "kb-section-icon") return paintSectionIcon(body);
  if (name === "kb-drama") return paintDrama(body, profile);
  if (name === "kb-stage") return `<div class="kb-stage">\n\n${body}\n\n</div>`;
  if (name === "kb-dash") return `<div class="kb-dash"></div>`;
  return body;
}

export function paintFencesLegacy(md, ctx = {}) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const printToPdf = ctx.printToPdf && typeof ctx.printToPdf === "object" ? ctx.printToPdf : {};
  return (md || "").replace(FENCE, (_m, name, inner) => {
    const body = stripBlockIds(
      peelInnerFenceTokens(String(inner || "").replace(/^\n/, "").replace(/\n$/, "")),
    );
    const tocEntries = Array.isArray(ctx.tocEntries) ? ctx.tocEntries : [];
    if (name === "kb-page-toc") return liftPageToc(body, printToPdf, profile, tocEntries, ctx.lexicon);
    if (name === "kb-list-item") return itemHtml(body, "kb-list-item", profile);
    if (name === "kb-solution") {
      return '<p class="kb-solution"><span class="kb-solution-label">解</span></p>';
    }
    if (name === "kb-solution-tail") return itemHtml(body, "kb-solution-tail", profile);
    if (name === "kb-lead") return paintLead(body, profile);
    if (name === "kb-center") return `<div class="kb-center">\n\n${body}\n\n</div>`;
    if (name === "kb-title") return `<div class="kb-title">\n\n${body}\n\n</div>`;
    if (name === "kb-subtitle") return `<div class="kb-subtitle">\n\n${body}\n\n</div>`;
    if (name === "kb-author") return `<div class="kb-author">\n\n${body}\n\n</div>`;
    if (name === "kb-section") return `<div class="kb-section">\n\n${body}\n\n</div>`;
    if (name === "kb-section-center") return `<div class="kb-section-center">\n\n${body}\n\n</div>`;
    if (name === "kb-verse") return `<div class="kb-verse">\n\n${body}\n\n</div>`;
    if (name === "kb-verse-center") return `<div class="kb-verse-center">\n\n${body}\n\n</div>`;
    if (name === "kb-date") return `<div class="kb-date">\n\n${body}\n\n</div>`;
    if (name === "kb-table-wrap") return `<div class="kb-table-wrap">${body}</div>`;
    if (name === "kb-section-icon") return paintSectionIcon(body);
    if (name === "kb-drama") return paintDrama(body, profile);
    if (name === "kb-stage") return `<div class="kb-stage">\n\n${body}\n\n</div>`;
    if (name === "kb-dash") return `<div class="kb-dash"></div>`;
    return body;
  });
}

export function paintFences(md, ctx = {}) {
  if (parserMode(ctx) === "legacy") return paintFencesLegacy(md, ctx);
  const text = String(md || "");
  const top = extractTopLevel(text);
  if (!top.length) return paintOfm(text, { escape: false });
  let out = "";
  let cursor = 0;
  for (const fence of top) {
    if (fence.start > cursor) out += paintOfm(text.slice(cursor, fence.start), { escape: false });
    out += paintOne(fence.name, text.slice(fence.innerStart, fence.innerEnd), ctx);
    cursor = fence.end;
  }
  if (cursor < text.length) out += paintOfm(text.slice(cursor), { escape: false });
  return out;
}

const THINK_LABEL = /想一想|书写表达/;

export function wrapChemMods(html) {
  const re =
    /<div class="kb-section-icon">[\s\S]*?<\/div>[\s\S]*?<div class="kb-dash"><\/div>/g;
  return (html || "").replace(re, (block) => {
    if ((block.match(/<div class="kb-section-icon">/g) || []).length > 1) {
      return block;
    }
    const cls = THINK_LABEL.test(block) ? "kb-chem-mod kb-chem-mod--think" : "kb-chem-mod";
    return `<div class="${cls}">\n${block}\n</div>`;
  });
}

export function stackWorkHeads(html) {
  return (html || "").replace(
    /(<div class="kb-title">(?:(?!<div )[\s\S])*<\/div>)(?:\s*(<div class="kb-subtitle">(?:(?!<div )[\s\S])*<\/div>))?(?:\s*(<div class="kb-author">(?:(?!<div )[\s\S])*<\/div>))?/g,
    (_all, title, subtitle, author) => {
      const parts = [title, subtitle, author].filter(Boolean);
      return `<div class="kb-work-head">\n${parts.join("\n")}\n</div>`;
    },
  );
}

export function stackVerseDates(html) {
  return (html || "").replace(
    /(<div class="kb-verse(?:-center)?">(?:(?!<div )[\s\S])*<\/div>)\s*(<div class="kb-date">(?:(?!<div )[\s\S])*<\/div>)/g,
    '<div class="kb-verse-stack">\n$1\n$2\n</div>',
  );
}
