const CJK_SPLIT =
  /([\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff，。；：、！？（）《》""''…—]+)/;
const BARE_CMD = /\\(?:[a-zA-Z]+|[{}\[\]])/;
const UNICODE_MATH = /[√∞∪∩≤≥≠παβγθ∈∉∅]/;
const LETTER_REL = /[a-zA-Z]\s*[=<>]/;
const DISPLAY_HINT = /\\begin\{|\\frac\{|\\sum(?![a-zA-Z])|\\int(?![a-zA-Z])/;
const PROTECTED_SOURCE =
  '\\$\\$[\\s\\S]+?\\$\\$|\\$[^$\\n]+?\\$|\\\\\\[[\\s\\S]+?\\\\\\]|\\\\\\([\\s\\S]+?\\\\\\)|!\\[[^\\]]*\\]\\([^)]+\\)|<div class="kb-table-wrap"[\\s\\S]+?<\\/div>|<div class="kb-page-toc-heading"[\\s\\S]+?<\\/div>|<table[\\s\\S]+?<\\/table>|<img\\b[^>]*>|<nav[\\s\\S]+?<\\/nav>|<p class="kb-(?:list-item|solution|solution-tail|lead|page-toc-heading)"[\\s\\S]+?<\\/p>';

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

export function wrapBareMath(md, profile = {}) {
  const raw = md || "";
  if (!raw) return raw;
  const extra = extraProtect(profile);
  const source = extra ? `${PROTECTED_SOURCE}|${extra}` : PROTECTED_SOURCE;
  const protectedRe = new RegExp(source, "gi");
  const out = [];
  let cursor = 0;
  raw.replace(protectedRe, (match, offset) => {
    out.push(wrapUnprotected(raw.slice(cursor, offset)));
    out.push(match);
    cursor = offset + match.length;
    return match;
  });
  out.push(wrapUnprotected(raw.slice(cursor)));
  return out.join("");
}
