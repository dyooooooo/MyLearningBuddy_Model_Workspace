const CJK_SPLIT =
  /([\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff，。；：、！？（）《》""''…—]+)/;
const BARE_CMD = /\\(?:[a-zA-Z]+|[{}\[\]])/;
const UNICODE_MATH = /[√∞∪∩≤≥≠παβγθ∈∉∅]/;
const LETTER_REL = /[a-zA-Z]\s*[=<>]/;
const DISPLAY_HINT = /\\begin\{|\\frac\{|\\sum(?![a-zA-Z])|\\int(?![a-zA-Z])/;
const PROTECTED_INLINE =
  '\\$\\$[\\s\\S]+?\\$\\$|\\$[^$\\n]+?\\$|\\\\\\[[\\s\\S]+?\\\\\\]|\\\\\\([\\s\\S]+?\\\\\\)|!\\[[^\\]]*\\]\\([^)]+\\)|<div class="kb-table-wrap"[\\s\\S]+?<\\/div>|<div class="kb-page-toc-heading"[\\s\\S]+?<\\/div>|<table[\\s\\S]+?<\\/table>|<img\\b[^>]*>|<nav[\\s\\S]+?<\\/nav>|<p class="kb-(?:list-item|solution|solution-tail|lead|page-toc-heading|speech|drama-aria|drama-yuan|drama-note)"[\\s\\S]+?<\\/p>|<a class="kb-wikilink"[\\s\\S]+?<\\/a>|<mark class="kb-ofm-highlight"[\\s\\S]+?<\\/mark>|<aside class="kb-ofm-callout"[\\s\\S]+?<\\/aside>';

function isMathRun(part) {
  const text = part.trim();
  if (!text || text.includes("$")) return false;
  if (/^https?:\/\//.test(text) || text.startsWith("/api/")) return false;
  if (BARE_CMD.test(text)) return true;
  if (UNICODE_MATH.test(text) && /[=<>+\-*/^_{}\\]|[a-zA-Z]/.test(text)) return true;
  if (LETTER_REL.test(text) && /[+\-*/^_{}\\]|\\/.test(text)) return true;
  if (/[a-zA-Z]\s*[<>]=?\s*[-+]?\d/.test(text)) return true;
  return false;
}

function wrapRun(part) {
  const text = part.trim();
  const lead = part.slice(0, part.length - part.trimStart().length);
  const tail = part.slice(part.trimEnd().length);
  if (DISPLAY_HINT.test(text) || text.includes("\n") || text.length > 80) {
    return `${lead}$$${text}$$${tail}`;
  }
  return `${lead}$${text}$${tail}`;
}

function wrapUnprotected(text) {
  if (!text) return text;
  return text.split(CJK_SPLIT).map((part) => (isMathRun(part) ? wrapRun(part) : part)).join("");
}

function extraProtect(profile) {
  const extras = [];
  for (const raw of profile.protect_selectors || []) {
    const cls = String(raw || "").replace(/[^a-zA-Z0-9_-]/g, "");
    if (!cls) continue;
    extras.push(`<[a-zA-Z]+[^>]*class="[^"]*${cls}[^"]*"[\\s\\S]+?<\\/[a-zA-Z]+>`);
  }
  return extras.join("|");
}

function balancedKbDivSpans(text) {
  const spans = [];
  const openRe = /<div class="kb-[^"]*"/gi;
  let match;
  while ((match = openRe.exec(text))) {
    const start = match.index;
    if (spans.some(([from, to]) => start >= from && start < to)) continue;
    let index = start + match[0].length;
    let depth = 1;
    while (index < text.length && depth > 0) {
      const nextOpen = text.indexOf("<div", index);
      const nextClose = text.indexOf("</div>", index);
      if (nextClose < 0) {
        depth = -1;
        break;
      }
      if (nextOpen >= 0 && nextOpen < nextClose) {
        depth += 1;
        index = nextOpen + 4;
      } else {
        depth -= 1;
        index = nextClose + 6;
      }
    }
    if (depth === 0) spans.push([start, index]);
  }
  return spans;
}

function regexSpans(text, source) {
  const spans = [];
  const re = new RegExp(source, "gi");
  let match;
  while ((match = re.exec(text))) {
    spans.push([match.index, match.index + match[0].length]);
    if (match[0].length === 0) re.lastIndex += 1;
  }
  return spans;
}

function mergeSpans(spans) {
  if (!spans.length) return [];
  const sorted = [...spans].sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const out = [];
  for (const [start, end] of sorted) {
    const last = out[out.length - 1];
    if (!last || start >= last[1]) {
      out.push([start, end]);
      continue;
    }
    last[1] = Math.max(last[1], end);
  }
  return out;
}

export function wrapBareMath(md, profile = {}) {
  const raw = md || "";
  if (!raw) return raw;
  const extra = extraProtect(profile);
  const source = extra ? `${PROTECTED_INLINE}|${extra}` : PROTECTED_INLINE;
  const spans = mergeSpans([...balancedKbDivSpans(raw), ...regexSpans(raw, source)]);
  const out = [];
  let cursor = 0;
  for (const [start, end] of spans) {
    out.push(wrapUnprotected(raw.slice(cursor, start)));
    out.push(raw.slice(start, end));
    cursor = end;
  }
  out.push(wrapUnprotected(raw.slice(cursor)));
  return out.join("");
}
