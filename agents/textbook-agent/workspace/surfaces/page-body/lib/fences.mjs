import { wrapBareMath } from "./bare-math.mjs";
import { liftPageToc } from "./page-toc.mjs";

export const FENCE_NAMES = new Set([
  "kb-center",
  "kb-verse",
  "kb-verse-center",
  "kb-list-item",
  "kb-lead",
  "kb-solution",
  "kb-solution-tail",
  "kb-page-toc",
  "kb-table-wrap",
]);

const FENCE = /::: (kb-[\w-]+)\r?\n([\s\S]*?)\r?\n:::/g;
const LEAD = /^((?:[\u4e00-\u9fff]{1,6})?定理|定义|证明|探究与实践)(?:\s+|$)/;

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

function itemHtml(text, cls, profile) {
  return `<p class="${cls}">${wrapBareMath(escapeText(text), profile)}</p>`;
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

export function paintFences(md, ctx = {}) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const printToPdf = ctx.printToPdf && typeof ctx.printToPdf === "object" ? ctx.printToPdf : {};
  return (md || "").replace(FENCE, (_m, name, inner) => {
    const body = String(inner || "").replace(/^\n/, "").replace(/\n$/, "");
    if (name === "kb-page-toc") return liftPageToc(body, printToPdf, profile);
    if (name === "kb-list-item") return itemHtml(body, "kb-list-item", profile);
    if (name === "kb-solution") {
      return '<p class="kb-solution"><span class="kb-solution-label">解</span></p>';
    }
    if (name === "kb-solution-tail") return itemHtml(body, "kb-solution-tail", profile);
    if (name === "kb-lead") return paintLead(body, profile);
    if (name === "kb-center") return `<div class="kb-center">\n\n${body}\n\n</div>`;
    if (name === "kb-verse") {
      return `<div class="kb-verse"><div class="kb-verse-inner">\n\n${body}\n\n</div></div>`;
    }
    if (name === "kb-verse-center") return `<div class="kb-verse-center">\n\n${body}\n\n</div>`;
    if (name === "kb-table-wrap") return `<div class="kb-table-wrap">${body}</div>`;
    return body;
  });
}
