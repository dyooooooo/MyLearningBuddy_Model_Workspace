import { wrapBareMath } from "./bare-math.mjs";

const SPAN =
  /\{\s*['"]type['"]\s*:\s*['"][^'"]+['"]\s*,\s*['"]content['"]\s*:\s*['"]([\s\S]+?)['"]\s*\}/g;
const HEADING =
  /^(?:第[一二三四五六七八九十百零\d]+章.*|内容提要|复习题|\d+\.\d+\s+\S.*|例\s*\d+.*|练习\s*[\d.()]+.*|定理\s*\d+.*|\d+\s+[\u4e00-\u9fff]{2,16})$/;
const MARKER = /(?<![\w.\-\u4e00-\u9fff])[（(]\s*([0-9０-９]+)\s*[）)]/g;
const SHORT_FORMULA_ALT = /^[\[\(][^\[\]\(\)]{0,16}[\]\)]$/;
const CHAPTER_TITLE = /^[\u4e00-\u9fff、]{2,20}$/;
const LEAD = /^((?:[\u4e00-\u9fff]{1,6})?定理|定义|证明|探究与实践)(?:\s+|$)/;
const SOLUTION = /^解(?:[\s\u3000]+|(?=[（(]))(.*)$/;
const SKIP_VISIBLE_CAPTION = new Set(["插图", "公式", "表格"]);

function spansIn(text) {
  return [...text.matchAll(new RegExp(SPAN.source, "g"))].map((m) => m[1].trim()).filter(Boolean);
}

function safeAlt(text) {
  const t = (text || "").trim() || "插图";
  if (t.startsWith("图") || t.startsWith("表")) return t.replace(/\]/g, "］");
  if (SHORT_FORMULA_ALT.test(t)) {
    return t.replace(/\[/g, "［").replace(/\]/g, "］").replace(/\(/g, "（").replace(/\)/g, "）");
  }
  return t.replace(/\]/g, "］");
}

function pickCaption(alt) {
  const spans = spansIn(alt);
  if (!spans.length) return safeAlt(alt);
  const fig = spans.find((t) => /^(?:图|表)\s*\d/.test(t));
  if (fig) return fig.replace(/\]/g, "］");
  const words = spans.filter((t) => !/^[\[\(\$]/.test(t) && t.length > 2);
  return safeAlt(words[0] || spans[spans.length - 1] || "插图");
}

export function fixMineruCaptions(md) {
  let text = md || "";
  text = text.replace(/!\[([\s\S]*?)\]\(([^)]+)\)/g, (_m, alt, url) => {
    if (!alt.includes("{")) {
      const cleaned = alt.includes("[") || alt.endsWith("]") ? safeAlt(alt) : alt;
      return `![${cleaned}](${url})`;
    }
    return `![${pickCaption(alt)}](${url})`;
  });
  text = text.replace(/(!\[[^\]]+\]\([^)]+\))\s*\n+\*\{\s*['"]type['"][\s\S]{0,200}?\*/g, "$1");
  text = text.replace(new RegExp(SPAN.source, "g"), "$1");
  text = text.replace(
    /(!\[([^\]]+)\]\([^)]+\))\s*\n+\*([\s\S]{0,120}?)\*/g,
    (m, img, alt, inner) => (inner.includes(alt) || /图\s*\d/.test(inner) ? img : m),
  );
  return restoreVisibleCaptions(text);
}

function restoreVisibleCaptions(md) {
  return (md || "").replace(/!\[([^\]]+)\]\(([^)]+)\)(?!\s*\n+\*)/g, (all, alt) => {
    const t = (alt || "").trim();
    if (!t || SKIP_VISIBLE_CAPTION.has(t)) return all;
    return `${all}\n\n*${t}*`;
  });
}

export function dropNoiseLines(md) {
  return (md || "")
    .replace(/^\s*#+\s*$/gm, "")
    .replace(/^\s*第\s*$/gm, "")
    .replace(/^\s*[?？]\s*$/gm, "")
    .replace(/^\s*例\s*$/gm, "");
}

export function dropFormulaShots(md) {
  return (md || "").replace(
    /!\[([^\]]*)\]\([^)]+\)[ \t]*\n+(?=[^\n]*(?:\\[a-zA-Z{]|[_^]\s*\{))/g,
    (m, alt) => (/^图/.test(String(alt).trim()) ? m : ""),
  );
}

export function looksLikeHeading(text) {
  const t = (text || "").trim();
  if (!t || t.startsWith("#") || t.startsWith("<")) return false;
  if (/^(?:例|练习|定理)/.test(t)) return t.length <= 56 && HEADING.test(t);
  return t.length <= 40 && HEADING.test(t);
}

export function liftHeadings(md) {
  const lines = (md || "").split("\n");
  const out = [];
  let prevImg = false;
  for (const line of lines) {
    const t = line.trim();
    if (looksLikeHeading(line)) {
      out.push(`### ${t}`);
      prevImg = false;
      continue;
    }
    if (prevImg && CHAPTER_TITLE.test(t) && !looksLikeHeading(t)) {
      out.push(`### ${t}`);
      prevImg = false;
      continue;
    }
    out.push(line);
    if (t) prevImg = t.startsWith("![");
  }
  return out.join("\n");
}

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

function escapeText(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function itemHtml(text, cls, profile) {
  return `<p class="${cls}">${wrapBareMath(escapeText(text), profile)}</p>`;
}

export function polishLists(md, profile = {}) {
  const out = [];
  for (const raw of (md || "").split("\n")) {
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith("<") || trimmed.startsWith("!") || trimmed.startsWith("#")) {
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
    if (solution) {
      out.push('<p class="kb-solution"><span class="kb-solution-label">解</span></p>');
    }
    if (chunks) {
      for (const chunk of chunks) out.push(itemHtml(unifyMarker(chunk), "kb-list-item", profile));
      continue;
    }
    if (/^[（(]\s*[0-9０-９]+\s*[）)]/.test(body) && !hasPackedMarkers(body)) {
      out.push(itemHtml(unifyMarker(body), "kb-list-item", profile));
      continue;
    }
    if (/^\d+\.\s+[\u4e00-\u9fff]/.test(body) && !/^\d+\.\d+/.test(body)) {
      out.push(itemHtml(body, "kb-list-item", profile));
      continue;
    }
    if (solution && body) {
      out.push(itemHtml(body, "kb-solution-tail", profile));
      continue;
    }
    out.push(raw);
  }
  return out.join("\n");
}

export function polishLeads(md, profile = {}) {
  const out = [];
  for (const raw of (md || "").split("\n")) {
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith("<") || trimmed.startsWith("!") || trimmed.startsWith("#")) {
      out.push(raw);
      continue;
    }
    const match = LEAD.exec(trimmed);
    if (!match) {
      out.push(raw);
      continue;
    }
    const rest = trimmed.slice(match[0].length).trim();
    const label = match[1];
    if (rest) {
      out.push(
        `<p class="kb-lead"><span class="kb-lead-label">${escapeText(label)}</span> ${wrapBareMath(escapeText(rest), profile)}</p>`,
      );
    } else {
      out.push(`<p class="kb-lead"><span class="kb-lead-label">${escapeText(label)}</span></p>`);
    }
  }
  return out.join("\n");
}

export function polishPageProse(md) {
  return dropFormulaShots(dropNoiseLines(fixMineruCaptions(md)));
}
