import { renderLatexToHtml } from "./mathjax-render.mjs";
import { printTocPage, renderPrintTocPage } from "./print-toc.mjs";

const STRUCTURAL = new Set(["title", "table", "interline_equation", "image", "chart"]);
const STOP_KINDS = new Set(["box", "figure", "ref"]);
const _mathCache = new Map();

export function irAssetUrl(bookId, path) {
  const raw = String(path || "").replace(/\\/g, "/").trim();
  if (!raw) return "";
  if (raw.startsWith("/api/") || raw.startsWith("http://") || raw.startsWith("https://")) {
    return raw;
  }
  const name = raw.split("/").pop() || "";
  if (!name) return "";
  const key = `kb/pdfs/${bookId}/assets/mineru/${name}`;
  return `/api/v1/kb/proxy/asset?key=${encodeURIComponent(key)}`;
}

export function stripBoxPunct(text) {
  return String(text || "").trim().replace(/[：:﹕]+$/g, "");
}

function isExactBoxLabel(blk) {
  const kind = String(blk && blk.box_kind || "").trim();
  if (!kind) return false;
  return stripBoxPunct(blk.text || "") === kind;
}

function escapeHtml(text) {
  return String(text || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function attr(text) {
  return escapeHtml(text);
}

function sizeAttrs(wf, fillSlot) {
  if (fillSlot || typeof wf !== "number" || !(wf > 0)) {
    return { cls: "", style: "" };
  }
  return { cls: " kb-ir-sized", style: ` style="--kb-wf:${wf}"` };
}

function blockAttr(id) {
  return id ? ` data-kb-block="${attr(id)}"` : "";
}

function inlineImg(blk, bookId) {
  const src = irAssetUrl(bookId, blk.asset || "");
  if (!src) return "";
  const alt = attr(blk.caption || "图");
  return `<img class="kb-ir-inline" src="${attr(src)}" alt="${alt}">`;
}

function lineRows(lines) {
  const out = [];
  for (const line of lines || []) {
    if (line && typeof line === "object" && !Array.isArray(line) && Array.isArray(line.spans)) {
      out.push({ bbox: line.bbox, spans: line.spans });
    } else if (Array.isArray(line)) {
      out.push({ bbox: null, spans: line });
    }
  }
  return out;
}

function paintMath(tex, display) {
  const src = String(tex || "").trim();
  if (!src) return "";
  const key = `${display ? "1" : "0"}\0${src}`;
  const hit = _mathCache.get(key);
  if (hit) return hit;
  const cls = display ? "math-inline math--display math-typeset" : "math-inline math-typeset";
  const { html, error } = renderLatexToHtml(src, display);
  const out = !error && html
    ? `<span class="${cls}" data-latex-src="${attr(src)}">${html}</span>`
    : `<span class="${cls} math-error" data-latex-src="${attr(src)}">${escapeHtml(latexToVisible(src) || src)}</span>`;
  _mathCache.set(key, out);
  return out;
}

function firstEqTex(blk) {
  const latex = String(blk?.latex || "").trim();
  if (latex) return latex;
  for (const row of lineRows(blk?.lines)) {
    for (const span of row.spans || []) {
      if (span.t !== "eq" && span.t !== "ieq") continue;
      const tex = String(span.latex || span.c || "").trim();
      if (tex) return tex;
    }
  }
  const text = String(blk?.text || "").trim();
  const display = text.match(/\$\$([\s\S]+?)\$\$/);
  if (display) return display[1].trim();
  const inline = text.match(/\$([^$]+)\$/);
  if (inline) return inline[1].trim();
  return "";
}

function spanHtml(span, bookId) {
  const kind = span.t || "text";
  if (kind === "eq" || kind === "ieq") {
    const tex = (span.latex || span.c || "").trim();
    return tex ? paintMath(tex, kind === "ieq") : "";
  }
  if (kind === "img" && span.c) {
    const src = irAssetUrl(bookId, span.c);
    return src ? `<img class="kb-ir-inline" src="${attr(src)}" alt="">` : "";
  }
  if (kind === "sub") return `<sub>${escapeHtml(span.c || "")}</sub>`;
  if (kind === "sup") return `<sup>${escapeHtml(span.c || "")}</sup>`;
  if (kind === "tab") return `<span class="kb-ir-tab"></span>`;
  if (span.c) return escapeHtml(span.c);
  return "";
}

function contentSpans(lines) {
  const out = [];
  for (const row of lineRows(lines)) {
    for (const span of row.spans || []) {
      if (!span) continue;
      if (span.c || span.latex || span.t === "tab" || span.t === "img") out.push(span);
    }
  }
  return out;
}

function lineCap(boxW, fs) {
  const width = Number(boxW) || 0;
  const size = Number(fs) || 0;
  if (!(width > 0) || !(size > 0)) return 0;
  return (width / size) * 0.96;
}

function wrapLineCount(text, boxW, fs, indent) {
  const full = lineCap(boxW, fs);
  const pad = Math.max(0, Number(indent) || 0);
  const first = pad > 0 ? lineCap(Math.max(1, (Number(boxW) || 0) - pad), fs) : full;
  const units = inkUnits(text || "");
  if (!(full > 0) || !(units > 0)) return 1;
  if (!(first > 0)) return Math.max(1, Math.ceil(units / full - 1e-9));
  if (units <= first + 1e-9) return 1;
  return 1 + Math.max(1, Math.ceil((units - first) / full - 1e-9));
}

function spanInkUnits(span) {
  const kind = span && span.t || "text";
  if (kind === "eq" || kind === "ieq") return inkUnits(latexToVisible(span.latex || span.c || ""));
  if (kind === "img") return 1;
  if (kind === "tab") return 2;
  return inkUnits(span && span.c || "");
}

function wrapSpansToRows(spans, firstCap, restCap) {
  const first = Number(firstCap) || 0;
  const rest = restCap == null ? first : Number(restCap) || 0;
  if (!(first > 0)) return [];
  const rows = [];
  let cur = [];
  let used = 0;
  const capOf = () => (rows.length ? rest || first : first);
  const flush = () => {
    if (!cur.length) return;
    rows.push(cur);
    cur = [];
    used = 0;
  };
  const addText = (ch) => {
    const u = inkUnits(ch);
    if (cur.length && used + u > capOf() + 1e-9) flush();
    const last = cur[cur.length - 1];
    if (last && (last.t || "text") === "text") {
      last.c = String(last.c || "") + ch;
    } else {
      cur.push({ t: "text", c: ch });
    }
    used += u;
  };
  for (const span of spans || []) {
    const kind = span.t || "text";
    if (kind === "text" && span.c) {
      for (const ch of [...String(span.c)]) addText(ch);
      continue;
    }
    const u = spanInkUnits(span);
    if (cur.length && used + u > capOf() + 1e-9) flush();
    cur.push(span);
    used += u;
  }
  flush();
  return rows;
}

function extraLineFitsBox(boxH, lineH, lines) {
  const h = Number(boxH) || 0;
  const lh = Number(lineH) || 0;
  const n = Math.round(Number(lines) || 0);
  if (!(h > 0) || !(lh > 0) || n < 1) return false;
  return Math.round(h / lh) >= n;
}

function resolvePrintedLines(original, boxH, lineH) {
  const orig = Math.round(Number(original) || 0);
  if (orig >= 1) return orig;
  if (extraLineFitsBox(boxH, lineH, 2)) return 0;
  if ((Number(boxH) || 0) > 0 && (Number(lineH) || 0) > 0) return 1;
  return 0;
}

function shouldFoldOneExtra(calculated, original, boxH, lineH) {
  const orig = resolvePrintedLines(original, boxH, lineH);
  const calc = Math.round(Number(calculated) || 0);
  if (orig < 1) return false;
  if (extraLineFitsBox(boxH, lineH, orig + 1)) return false;
  if (calc === orig + 1) return true;
  return calc <= orig && orig === 1;
}

function foldLockN(calculated, original, boxH, lineH) {
  if (!shouldFoldOneExtra(calculated, original, boxH, lineH)) return 0;
  return resolvePrintedLines(original, boxH, lineH);
}

function foldIfOneExtra(rows, original) {
  const orig = Math.round(Number(original) || 0);
  if (orig < 1 || rows.length !== orig + 1) return rows;
  const kept = rows.slice(0, -1).map((spans) => spans.slice());
  kept[kept.length - 1].push(...rows[rows.length - 1]);
  return kept;
}

function paintLineRows(rows, bookId) {
  return rows.map((spans) => {
    const inner = spans.map((span) => spanHtml(span, bookId)).join("");
    return `<span class="kb-ir-line">${inner}</span>`;
  }).join("");
}

function foldWrappedHtml(spans, bookId, opts = {}) {
  const orig = Math.round(Number(opts.lockN) || 0);
  const boxW = Number(opts.boxW) || 0;
  const indent = Math.max(0, Number(opts.indent) || 0);
  const firstCap = lineCap(Math.max(1, boxW - indent), opts.fs);
  const restCap = lineCap(boxW, opts.fs) || firstCap;
  if (!opts.lockLines || orig < 1 || !(firstCap > 0) || !spans.length) return "";
  const rows = wrapSpansToRows(spans, firstCap, restCap);
  if (rows.length !== orig + 1) return "";
  return paintLineRows(foldIfOneExtra(rows, orig), bookId);
}

function dumpLinesHtml(lines, bookId) {
  const bits = [];
  for (const row of lineRows(lines)) {
    for (const span of row.spans || []) {
      const html = spanHtml(span, bookId);
      if (html) bits.push(html);
    }
  }
  return bits.join("");
}

function linesHtml(lines, bookId, opts = {}) {
  if (!lines || !lines.length) return "";
  return foldWrappedHtml(contentSpans(lines), bookId, opts) || dumpLinesHtml(lines, bookId);
}

function alignClass(blk) {
  const align = String(blk.align || "").trim();
  if (align === "center" || align === "right" || align === "left") {
    return ` kb-ir-align-${align}`;
  }
  return "";
}

function indentClass(blk) {
  const marked = blk.indented === true || Number(blk.indent || 0) >= 12;
  return marked ? " kb-ir-indent" : "";
}

function classAttr(cls) {
  const bits = String(cls || "").trim();
  return bits ? ` class="${bits}"` : "";
}

function paragraph(blk, bookId, extras = "", tail = "", opts = {}) {
  const text = (blk.text || "").trim();
  const inner = linesHtml(blk.lines, bookId, opts)
    || foldWrappedHtml(text ? [{ t: "text", c: text }] : [], bookId, opts)
    || escapeHtml(text);
  const body = `${extras}${inner}${tail}`.trim();
  if (!body) return "";
  const extra = opts.sheet ? alignClass(blk) : alignClass(blk) + indentClass(blk);
  return `<p${classAttr(extra.trim())}${blockAttr(blk.id)}>${body}</p>`;
}

function figureHtml(blk, bookId, cell = false, opts = {}) {
  const src = irAssetUrl(bookId, blk.asset || "");
  const caption = (blk.caption || "").trim();
  const sized = sizeAttrs(blk.width_frac, opts.fillSlot);
  const cls = `${cell ? "kb-ir-figcell" : "kb-ir-fig"}${sized.cls}`;
  const alt = attr(caption || "插图");
  const img = src ? `<img src="${attr(src)}" alt="${alt}">` : "";
  const cap = caption && !opts.sheet ? `<figcaption>${escapeHtml(caption)}</figcaption>` : "";
  return `<figure class="${cls}"${sized.style}${blockAttr(blk.id)}>${img}${cap}</figure>`;
}

function rowHtml(cells, bookId) {
  const inner = cells.map((cell) => figureHtml(cell, bookId, true)).join("");
  return `<div class="kb-ir-figrow">${inner}</div>`;
}

function boxHtml(blk, children) {
  const kind = (blk.box_kind || "").trim();
  const raw = (blk.text || "").trim();
  const extra = stripBoxPunct(raw) === kind ? "" : raw;
  const head = kind ? `<header class="kb-ir-box-title">${escapeHtml(kind)}</header>` : "";
  const self = extra ? `<p>${escapeHtml(extra)}</p>` : "";
  return `<aside class="kb-ir-box"${blockAttr(blk.id)}>${head}${self}${children}</aside>`;
}

function eqHtml(blk, bookId) {
  const latex = firstEqTex(blk);
  if (latex) {
    const inner = paintMath(latex, eqUseDisplay(boxOf(blk && blk.bbox), latex));
    return inner ? `<div class="kb-ir-eq"${blockAttr(blk.id)}>${inner}</div>` : "";
  }
  const text = (blk.text || "").trim();
  return text ? `<div class="kb-ir-eq"${blockAttr(blk.id)}>${escapeHtml(text)}</div>` : "";
}

function titleHtml(blk, bookId, opts = {}) {
  const level = Math.max(1, Math.min(6, Number(blk.level) || 2));
  // 标题也要走 span：公式当场画完、下标出真 <sub>、收口的两栏出制表位。
  // 只读 blk.text 会把这些统统压成转义字面量（$^{①}$ 直接印在 h2 里）。
  const text = (blk.text || "").trim();
  const inner = linesHtml(blk.lines, bookId, opts)
    || foldWrappedHtml(text ? [{ t: "text", c: text }] : [], bookId, opts)
    || escapeHtml(text);
  if (!inner) return "";
  return `<h${level}${classAttr(alignClass(blk).trim())}${blockAttr(blk.id)}>${inner}</h${level}>`;
}

const TABLE_TAGS = new Set([
  "table", "thead", "tbody", "tr", "td", "th", "caption", "colgroup", "col",
  "br", "sub", "sup", "b", "i", "em", "strong", "img",
]);
const TABLE_ALIGN = new Set(["left", "right", "center"]);

function tableImgSrc(bookId, raw) {
  const src = String(raw || "").trim();
  if (!src || /^(javascript|data|vbscript):/i.test(src)) return "";
  const name = src.replace(/\\/g, "/").split("/").pop() || "";
  if (!/\.(?:jpe?g|png|gif|webp)$/i.test(name)) return "";
  return irAssetUrl(bookId, src);
}

function sanitizeTableHtml(raw, bookId) {
  const html = String(raw || "");
  if (!html) return "";
  if (/<!--|<!doctype|<script|<style|<iframe/i.test(html)) return escapeHtml(html);
  const tagRe = /<\/?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>/g;
  let m;
  while ((m = tagRe.exec(html))) {
    if (!TABLE_TAGS.has(m[1].toLowerCase())) return escapeHtml(html);
  }
  return html.replace(/<\/?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>/g, (all, name, attrs) => {
    const n = name.toLowerCase();
    if (all.startsWith("</")) return `</${n}>`;
    const kept = [];
    const attrRe = /([a-zA-Z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>=]+)))?/g;
    let a;
    while ((a = attrRe.exec(attrs || ""))) {
      const key = a[1].toLowerCase();
      const val = a[2] ?? a[3] ?? a[4] ?? "";
      if ((key === "colspan" || key === "rowspan") && /^\d+$/.test(val) && Number(val) > 0) {
        kept.push(`${key}="${val}"`);
      } else if (key === "align" && TABLE_ALIGN.has(val.toLowerCase())) {
        kept.push(`align="${val.toLowerCase()}"`);
      } else if (n === "img" && key === "src") {
        const src = tableImgSrc(bookId, val);
        if (src) kept.push(`src="${attr(src)}"`);
      } else if (n === "img" && key === "alt") {
        kept.push(`alt="${attr(val)}"`);
      }
    }
    if (n === "img" && !kept.some((item) => item.startsWith("src="))) return "";
    const self = /\/>$/.test(all) ? " /" : "";
    return kept.length ? `<${n} ${kept.join(" ")}${self}>` : `<${n}${self}>`;
  });
}

function decodeTex(tex) {
  return String(tex || "")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&amp;/gi, "&")
    .replace(/&nbsp;/gi, " ")
    .trim();
}

function paintInlineMath(text) {
  const raw = String(text || "");
  if (!raw.includes("$") && !raw.includes("\\(") && !raw.includes("\\[")) return raw;
  return raw
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => paintMath(decodeTex(tex), true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => paintMath(decodeTex(tex), true))
    .replace(/\$([^$]+)\$/g, (_, tex) => paintMath(decodeTex(tex), false))
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, tex) => paintMath(decodeTex(tex), false));
}

function shieldDollarTex(html) {
  return String(html || "")
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => `$$${escapeHtml(tex)}$$`)
    .replace(/\$([^$]+)\$/g, (_, tex) => `$${escapeHtml(tex)}$`);
}

function paintTableHtml(html) {
  return String(html || "").replace(/(<[^>]+>)|([^<]+)/g, (all, tag, text) => (
    tag ? tag : paintInlineMath(text)
  ));
}

function markTableImageRows(html) {
  let has = false;
  const out = String(html || "").replace(/<tr\b([^>]*)>([\s\S]*?)<\/tr>/gi, (all, attrs, inner) => {
    if (!/<img\b/i.test(inner)) return all;
    has = true;
    if (/\bclass\s*=/i.test(attrs)) {
      return `<tr${attrs.replace(/class\s*=\s*(["'])/i, "class=$1kb-ir-img-row ")}>${inner}</tr>`;
    }
    return `<tr class="kb-ir-img-row"${attrs}>${inner}</tr>`;
  });
  if (!has) return out;
  return out.replace(/<table\b([^>]*)>/i, (all, attrs) => {
    if (/\bclass\s*=/i.test(attrs)) {
      return `<table${attrs.replace(/class\s*=\s*(["'])/i, "class=$1kb-ir-has-img ")}>`;
    }
    return `<table class="kb-ir-has-img"${attrs}>`;
  });
}

function tableHtml(blk, bookId, opts = {}) {
  const caption = (blk.caption || "").trim();
  const raw = (blk.table_html || "").trim();
  const box = boxOf(blk.bbox);
  const colWs = box ? tableColWidths(tableCells(raw), box.w) : [];
  const html = injectTableColGroup(
    markTableImageRows(paintTableHtml(shieldDollarTex(sanitizeTableHtml(raw, bookId)))),
    colWs,
    box && box.w,
  );
  const src = irAssetUrl(bookId, blk.asset || "");
  const bits = [];
  if (html) bits.push(html);
  else if (src) bits.push(`<img src="${attr(src)}" alt="${attr(caption || "表格")}">`);
  if (!bits.length && !caption) return "";
  const cap = caption && !opts.sheet ? `<figcaption>${escapeHtml(caption)}</figcaption>` : "";
  const sized = sizeAttrs(blk.width_frac, opts.fillSlot);
  return `<figure class="kb-ir-table${sized.cls}"${sized.style}${blockAttr(blk.id)}>${bits.join("")}${cap}</figure>`;
}

function refHtml(blk, bookId, opts = {}) {
  const text = (blk.text || "").trim();
  const inner = linesHtml(blk.lines, bookId, opts)
    || foldWrappedHtml(text ? [{ t: "text", c: text }] : [], bookId, opts)
    || escapeHtml(text);
  if (!inner) return "";
  return `<aside class="kb-ir-ref"${blockAttr(blk.id)}>${inner}</aside>`;
}

function renderOne(blk, bookId, extras = "", opts = {}, tail = "") {
  if (blk.kind === "box") {
    if (!isExactBoxLabel(blk) && blk.type === "title") return titleHtml(blk, bookId, opts);
    return boxHtml(blk, "");
  }
  if (blk.kind === "ref") return refHtml(blk, bookId, opts);
  if (blk.kind === "figure" || blk.type === "image" || blk.type === "chart") {
    return figureHtml(blk, bookId, false, opts);
  }
  if (blk.type === "table") return tableHtml(blk, bookId, opts);
  if (blk.type === "interline_equation") return eqHtml(blk, bookId);
  if (blk.type === "title") return titleHtml(blk, bookId, opts);
  return paragraph(blk, bookId, extras, tail, opts);
}

function isStopAfterBox(blk) {
  if (STOP_KINDS.has(blk.kind)) return true;
  if (blk.kind === "inline_image") return false;
  return STRUCTURAL.has(blk.type);
}

function renderStream(blocks, bookId, opts = {}) {
  const extras = {};
  const tails = {};
  const skip = new Set();
  for (let i = 0; i < blocks.length; i++) {
    const blk = blocks[i];
    if (blk.kind !== "inline_image") continue;
    const img = inlineImg(blk, bookId);
    if (!img) continue;
    const prevOk = i > 0 && blocks[i - 1].type === "text" && !skip.has(i - 1);
    const nextOk = i + 1 < blocks.length && blocks[i + 1].type === "text";
    if (prevOk) {
      tails[i - 1] = `${tails[i - 1] || ""}${img}`;
      skip.add(i);
    } else if (nextOk) {
      extras[i + 1] = `${img}${extras[i + 1] || ""}`;
      skip.add(i);
    }
  }

  const figRows = new Map();
  for (const blk of blocks) {
    if (blk.row == null) continue;
    const rid = Number(blk.row);
    const list = figRows.get(rid) || [];
    list.push(blk);
    figRows.set(rid, list);
  }
  const emitted = new Set();
  const parts = [];

  for (let i = 0; i < blocks.length; i++) {
    if (skip.has(i)) continue;
    const blk = blocks[i];
    if (blk.row != null) {
      const rid = Number(blk.row);
      if (emitted.has(rid)) continue;
      emitted.add(rid);
      const html = rowHtml(figRows.get(rid) || [blk], bookId);
      if (html) parts.push(html);
      continue;
    }
    if (blk.kind === "box") {
      if (!isExactBoxLabel(blk)) {
        const html = renderOne(blk, bookId, extras[i] || "", opts, tails[i] || "");
        if (html) parts.push(html);
        continue;
      }
      const kids = [];
      let j = i + 1;
      while (j < blocks.length) {
        if (skip.has(j)) {
          j += 1;
          continue;
        }
        const nxt = blocks[j];
        if (nxt.row != null || isStopAfterBox(nxt)) break;
        kids.push(renderOne(nxt, bookId, extras[j] || "", opts, tails[j] || ""));
        skip.add(j);
        j += 1;
      }
      parts.push(boxHtml(blk, kids.filter(Boolean).join("")));
      continue;
    }
    const html = renderOne(blk, bookId, extras[i] || "", opts, tails[i] || "");
    if (html) parts.push(html);
  }
  return parts.join("");
}

function chromeText(x) {
  if (x == null) return "";
  if (typeof x === "string" || typeof x === "number") return String(x).trim();
  return String(x.text || "").trim();
}

function chromeHead(chrome) {
  const headers = (chrome?.headers || []).map(chromeText).filter(Boolean);
  if (!headers.length) return "";
  return `<div class="kb-ir-chrome kb-ir-chrome-head">${headers.map((h) => `<span>${escapeHtml(h)}</span>`).join("")}</div>`;
}

function chromeFoot(chrome, folio) {
  const notes = (chrome?.footnotes || []).map(chromeText).filter(Boolean);
  const footers = (chrome?.footers || []).map(chromeText).filter(Boolean);
  const nums = (chrome?.page_numbers || []).map(chromeText).filter(Boolean);
  if (!notes.length && !footers.length && !nums.length) return "";
  const noteHtml = notes.map((n) => `<p class="kb-ir-footnote">${escapeHtml(n)}</p>`).join("");
  const tail = [...footers, ...nums].map((x) => `<span>${escapeHtml(x)}</span>`).join("");
  const sideClass =
    folio === "verso"
      ? " kb-ir-chrome-meta--left"
      : folio === "recto"
        ? " kb-ir-chrome-meta--right"
        : "";
  return `<div class="kb-ir-chrome kb-ir-chrome-foot">${noteHtml}${tail ? `<div class="kb-ir-chrome-meta${sideClass}">${tail}</div>` : ""}</div>`;
}

function pageWidthOf(page) {
  const ps = page?.page_size;
  if (!Array.isArray(ps) || ps.length < 1) return 0;
  const w = Number(ps[0]);
  return w > 1 ? w : 0;
}

function pageSizeOf(page) {
  const ps = page?.page_size;
  const pageW = Number(ps && ps[0]);
  const pageH = Number(ps && ps[1]);
  if (!(pageW > 1) || !(pageH > 1)) return null;
  return { pageW, pageH };
}

function boxOf(bb) {
  if (!Array.isArray(bb) || bb.length < 4) return null;
  const x = Number(bb[0]);
  const y = Number(bb[1]);
  const w = Number(bb[2]) - x;
  const h = Number(bb[3]) - y;
  if (!(w > 0) || !(h > 0)) return null;
  return { x, y, w, h };
}

function pct(n, den) {
  const v = (Number(n) / den) * 100;
  return `${Math.round(v * 10000) / 10000}%`;
}

function inkStyle(box, pageW, pageH, fs, indent, lineH) {
  const pos = `left:${pct(box.x, pageW)};top:${pct(box.y, pageH)};width:${pct(box.w, pageW)};height:${pct(box.h, pageH)}`;
  const bits = [pos];
  const size = Number(fs) || 0;
  const lead = Number(lineH) || 0;
  if (size > 0) bits.push(`--kb-fs:${size}`);
  if (lead > 0) bits.push(`--kb-lh:${lead}`);
  if (indent > 0) bits.push(`--kb-ind:${indent}`);
  return bits.join(";");
}

const INK_PAD = 0.08;
const INK_EQ_PAD = 0.12;
const INK_TABLE_PAD = 0.30;
const INK_TABLE_LINE = 16;
const INK_TABLE_IMG_MIN = 36;
const INK_TABLE_CELL_LH = 1.15;
const INK_TABLE_MATH_WIDE = 1.25;
const INK_TABLE_X_PAD = 10;
const INK_CAPTION_PAD = 0.30;
const INK_LH = 1 / (1 - INK_PAD);
const INK_LEAD = 12.5;
const INK_FS_MIN = 4;
const INK_H_MIN = 6;
const INK_H_MAX = 24;
const INK_H_MAX_TITLE = 80;
const TITLE_BAND_H = 40;

const LATEX_GLYPH = {
  in: "∈",
  notin: "∉",
  pi: "π",
  neq: "≠",
  ne: "≠",
  circ: "°",
  times: "×",
  div: "÷",
  leq: "≤",
  geq: "≥",
  le: "≤",
  ge: "≥",
  cdot: "·",
  pm: "±",
  mp: "∓",
  infty: "∞",
  subset: "⊂",
  subseteq: "⊆",
  cup: "∪",
  cap: "∩",
  emptyset: "∅",
  to: "→",
  rightarrow: "→",
  leftarrow: "←",
  forall: "∀",
  exists: "∃",
  mid: "|",
  ldots: "…",
  cdots: "⋯",
  alpha: "α",
  beta: "β",
  gamma: "γ",
  delta: "δ",
  theta: "θ",
  lambda: "λ",
  mu: "μ",
  sigma: "σ",
  omega: "ω",
};

function latexToVisible(src) {
  let s = String(src || "");
  s = s.replace(/\\\{/g, "{").replace(/\\\}/g, "}");
  s = s.replace(/\\(left|right|displaystyle|textstyle|limits)\b/g, "");
  s = s.replace(/\\(,|;|!|:|quad|qquad)\b/g, " ");
  s = s.replace(
    /\\(mathbf|mathrm|mathit|mathbb|mathcal|boldsymbol|text|textrm|textbf)\{([^{}]*)\}/g,
    "$2",
  );
  s = s.replace(/\\frac\{([^{}]*)\}\{([^{}]*)\}/g, "$1/$2");
  s = s.replace(/\\sqrt\{([^{}]*)\}/g, "√$1");
  s = s.replace(/\^\{([^{}]*)\}/g, "$1");
  s = s.replace(/_\{([^{}]*)\}/g, "$1");
  s = s.replace(/\^([A-Za-z0-9])/g, "$1");
  s = s.replace(/_([A-Za-z0-9])/g, "$1");
  s = s.replace(/\\([a-zA-Z]+)/g, (_, name) => LATEX_GLYPH[name] || "□");
  s = s.replace(/[{}]/g, "");
  s = s.replace(/\\\\/g, " ");
  return s;
}

function visibleInk(text) {
  let s = String(text || "");
  s = s.replace(/\$\$([\s\S]*?)\$\$/g, (_, inner) => latexToVisible(inner));
  s = s.replace(/\$([^$]*)\$/g, (_, inner) => latexToVisible(inner));
  if (/\\[a-zA-Z]/.test(s)) s = latexToVisible(s);
  return s;
}

function inkUnits(text) {
  let u = 0;
  for (const ch of visibleInk(text)) {
    if (/\s/.test(ch)) u += 0.35;
    else if (/[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]/.test(ch)) u += 1;
    else u += 0.55;
  }
  return Math.max(0.01, u);
}

function isPrintBox(box) {
  return Boolean(box && box.w >= 6 && box.h >= 6);
}

function isTitleKey(key) {
  return String(key || "").startsWith("title") || key === "eq-display" || key === "folio" || key === "mark";
}

function padForKey(key) {
  if (key === "eq-display") return INK_EQ_PAD;
  if (String(key || "").startsWith("caption")) return INK_CAPTION_PAD;
  if (key === "note") return INK_CAPTION_PAD;
  return INK_PAD;
}

function heightVote(h, title = false, pad = INK_PAD) {
  if (!(h >= INK_H_MIN)) return null;
  const cap = title ? INK_H_MAX_TITLE : INK_H_MAX;
  if (h > cap) return null;
  const n = Math.max(1, Math.round(h * (1 - pad)));
  return n < INK_FS_MIN ? null : n;
}

function modeOf(votes) {
  if (!votes.length) return null;
  const counts = new Map();
  for (const v of votes) counts.set(v, (counts.get(v) || 0) + 1);
  const mid = [...votes].sort((a, b) => a - b)[Math.floor(votes.length / 2)];
  let best = votes[0];
  let bestN = 0;
  for (const [val, n] of counts) {
    if (n > bestN || (n === bestN && Math.abs(val - mid) < Math.abs(best - mid))) {
      best = val;
      bestN = n;
    }
  }
  return best;
}

function isInkGraphic(blk) {
  return (
    blk.kind === "figure" ||
    blk.kind === "inline_image" ||
    blk.type === "image" ||
    blk.type === "chart" ||
    blk.type === "interline_equation"
  );
}

const CAPTION_FIG = new Set(["image_caption", "chart_caption", "code_caption"]);
const CAPTION_FIG_NOTE = new Set(["image_footnote", "chart_footnote", "code_footnote"]);
const CAPTION_TABLE = new Set(["table_caption", "table_footnote"]);
const CAPTION_TYPES = new Set([...CAPTION_FIG, ...CAPTION_FIG_NOTE, ...CAPTION_TABLE]);
const CAPTION_RE = /^(图|表)\s*[\d０-９][\d０-９.\-—－()（）]*$/;
const EXAMPLE_RE = /^例\s*[\d０-９一二三四五六七八九十]/;
const EXERCISE_RE = /^(练习|习题|复习题)/;

function isCaptionText(text) {
  return CAPTION_RE.test(String(text || "").trim());
}

function captionClass(type, text) {
  const kind = String(type || "");
  if (CAPTION_FIG_NOTE.has(kind)) return "caption-fig-note";
  if (CAPTION_TABLE.has(kind)) return "caption-table";
  if (CAPTION_FIG.has(kind)) return "caption-fig";
  const raw = String(text || "").trim();
  if (/^表\s*[\d０-９]/.test(raw)) return "caption-table";
  if (/^图\s*[\d０-９]/.test(raw)) return "caption-fig";
  return "caption-fig";
}

function isExampleHead(text) {
  const t = String(text || "").trim();
  return EXAMPLE_RE.test(t) || EXERCISE_RE.test(t);
}

function isExerciseHead(text) {
  return EXERCISE_RE.test(String(text || "").trim());
}

function isAsideBox(blk, box) {
  if (blk.kind === "box") return true;
  if (!box || !(box.w > 0) || !(box.h > 0)) return false;
  return box.w < 140 && box.h >= 28 && box.w / box.h < 3.5;
}

function isCloseId(id) {
  return /^kb-close-/.test(String(id || ""));
}

function inkClass(blk) {
  if (blk.kind === "ref") return "ref";
  if (blk.type === "title") {
    if (isExerciseHead(blockText(blk))) return "title-3";
    const lv = Math.max(1, Math.min(6, Number(blk.level) || 2));
    return `title-${lv}`;
  }
  if (blk.type === "table") return "table";
  if (blk.type === "interline_equation") return "eq";
  if (CAPTION_TYPES.has(String(blk.type || "")) || isCaptionText(blk.text)) {
    return captionClass(blk.type, blk.text);
  }
  const slot = String(blk.slot || "");
  if (slot) {
    if (slot === "aside" && blk.kind !== "box") return "aside";
  } else {
    const box = boxOf(blk.bbox);
    if (isAsideBox(blk, box)) return blk.kind === "box" ? "box" : "aside";
  }
  if (isExampleHead(blockText(blk))) return "example";
  return "body";
}

function fontKey(cls) {
  if (cls === "title-4" || cls === "title-5" || cls === "title-6") return "title-3";
  if (cls === "mark") return "header";
  if (cls === "table") return "table-cell";
  if (cls === "eq") return "eq-display";
  if (cls === "caption") return "caption-fig";
  return cls;
}

function chromeClass(key, item) {
  if (key === "page_numbers") return "folio";
  if (key === "footers") return "footer";
  if (key === "footnotes") return "note";
  const text = chromeText(item).replace(/\s/g, "");
  const box = boxOf(item && item.bbox);
  if (box && text.length <= 2 && box.h > 28) return "mark";
  return "header";
}

function blockText(blk) {
  const t = String(blk.text || "").trim();
  if (t) return t;
  return lineRows(blk.lines).map(rowText).join("");
}

function tablePlain(html) {
  return tableCellText(html);
}

function tableCellText(raw) {
  return String(raw || "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&gt;/gi, ">")
    .replace(/&lt;/gi, "<")
    .replace(/&amp;/gi, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function tableRowCount(html) {
  return Math.max(1, (String(html || "").match(/<tr\b/gi) || []).length);
}

function tableCells(html) {
  const rows = [];
  const taken = [];
  const trRe = /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi;
  let tr;
  let r = 0;
  while ((tr = trRe.exec(String(html || "")))) {
    const cells = [];
    const cellRe = /<(td|th)\b([^>]*)>([\s\S]*?)<\/\1>/gi;
    let cell;
    let c = 0;
    taken[r] = taken[r] || [];
    const skipTaken = () => {
      while (taken[r][c]) c += 1;
    };
    while ((cell = cellRe.exec(tr[1]))) {
      skipTaken();
      const spanM = /colspan\s*=\s*["']?(\d+)/i.exec(cell[2] || "");
      const rowM = /rowspan\s*=\s*["']?(\d+)/i.exec(cell[2] || "");
      const span = spanM ? Math.max(1, Number(spanM[1]) || 1) : 1;
      const rowSpan = rowM ? Math.max(1, Number(rowM[1]) || 1) : 1;
      cells.push({
        text: tableCellText(cell[3]),
        hasImg: /<img\b/i.test(cell[3]),
        span,
        rowSpan,
        col: c,
      });
      for (let i = 0; i < rowSpan; i += 1) {
        taken[r + i] = taken[r + i] || [];
        for (let j = 0; j < span; j += 1) taken[r + i][c + j] = true;
      }
      c += span;
    }
    if (cells.length) rows.push(cells);
    r += 1;
  }
  return rows;
}

function tableColCount(rows) {
  let n = 1;
  for (const row of rows) {
    for (const cell of row) {
      n = Math.max(n, (Number(cell.col) || 0) + Math.max(1, Number(cell.span) || 1));
    }
  }
  return n;
}

function tableColWidths(rows, boxW) {
  const cols = Math.max(1, tableColCount(rows));
  const width = Math.max(1, Number(boxW) || 0);
  const weights = Array.from({ length: cols }, () => 1);
  const shortU = Array.from({ length: cols }, () => 0);
  for (const row of rows || []) {
    for (const cell of row) {
      const c0 = Number(cell.col) || 0;
      const span = Math.max(1, Number(cell.span) || 1);
      if (cell.hasImg) {
        for (let i = 0; i < span && c0 + i < cols; i += 1) weights[c0 + i] += 0.9;
        continue;
      }
      if (span !== 1) continue;
      const u = inkUnits(visibleInk(cell.text || ""));
      if (u <= 14) shortU[c0] = Math.max(shortU[c0], u);
      if (u > 6) weights[c0] += Math.min(0.8, u / 24);
    }
  }
  const weighted = (mins) => {
    const slack = width - mins.reduce((a, b) => a + b, 0);
    if (!(slack >= 0)) return null;
    const sum = weights.reduce((a, b) => a + b, 0) || cols;
    return weights.map((w, i) => mins[i] + slack * w / sum);
  };
  for (const hint of [11, 10, 9, 8]) {
    const mins = shortU.map((u) => (u > 0 ? u * hint / 0.96 + INK_TABLE_X_PAD : 0));
    const got = weighted(mins);
    if (got) return got;
  }
  const sum = weights.reduce((a, b) => a + b, 0) || cols;
  return weights.map((w) => width * w / sum);
}

function tableCellWidth(cell, colWs) {
  const c0 = Number(cell && cell.col) || 0;
  const span = Math.max(1, Number(cell && cell.span) || 1);
  let w = 0;
  for (let i = 0; i < span; i += 1) w += Number(colWs[c0 + i]) || 0;
  return w;
}

function injectTableColGroup(html, colWs, boxW) {
  if (!html || !colWs.length || !(boxW > 0) || /<colgroup\b/i.test(html)) return html;
  const cols = colWs.map((w) => `<col style="width:${round2(w / boxW * 100)}%" />`).join("");
  return html.replace(/<table\b([^>]*)>/i, `<table$1><colgroup>${cols}</colgroup>`);
}

function tableRowHeight(box, html) {
  if (!box || !(box.h > 0)) return 0;
  const nRows = Math.max(1, tableCells(html).length || tableRowCount(html));
  return box.h / nRows;
}

function tableLineCount(cell) {
  const n = Number(cell && cell.line_count);
  if (Number.isFinite(n) && n >= 1) return Math.round(n);
  const raw = String((cell && cell.text) || "").trim();
  if (!raw) return 1;
  const breaks = (raw.match(/<br\s*\/?>|\n/gi) || []).length;
  return Math.max(1, breaks + 1);
}

function tableCellWrapLines(text, width, fs = 11) {
  const fromBr = tableLineCount({ text });
  const inner = Math.max(8, Number(width) || 0) - INK_TABLE_X_PAD;
  if (!(inner > 0)) return fromBr;
  const wrapped = wrapLineCount(visibleInk(text || ""), inner, fs, 0);
  return Math.max(fromBr, wrapped);
}

function tableMathChunks(text) {
  const raw = String(text || "");
  const out = [];
  const re = /\$\$([\s\S]+?)\$\$|\$([^$]+)\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g;
  let m;
  while ((m = re.exec(raw))) {
    const tex = m[1] || m[2] || m[3] || m[4] || "";
    const vis = latexToVisible(tex);
    if (vis.trim()) out.push(vis);
  }
  return out;
}

function tableCellInnerW(colW, span) {
  return Math.max(8, colW * Math.max(1, Number(span) || 1) - INK_TABLE_X_PAD);
}

function tableCellFitsWidth(text, width, fs) {
  const inner = Math.max(8, Number(width) || 0);
  const chunks = tableMathChunks(text);
  const tokens = chunks.length ? chunks : [visibleInk(text || "")];
  const unbreak = chunks.length ? chunks : [];
  for (const token of unbreak) {
    if (inkUnits(token) * fs * INK_TABLE_MATH_WIDE > inner + 1e-9) return false;
  }
  if (!unbreak.length && tokens[0]) {
    const units = inkUnits(tokens[0]);
    const cap = lineCap(inner, fs);
    if (!(cap > 0)) return false;
  }
  return true;
}

function tableNeededHeight(rows, colWs, fs, pad) {
  const slack = 1 - Math.max(0, Math.min(0.5, Number(pad) || 0));
  const lineBox = fs / slack;
  let h = 0;
  for (const row of rows || []) {
    if (tableRowIsImage(row)) {
      h += INK_TABLE_IMG_MIN;
      continue;
    }
    let lines = 1;
    for (const cell of row) {
      lines = Math.max(lines, tableCellWrapLines(cell.text, tableCellWidth(cell, colWs), fs));
    }
    h += lines * lineBox;
  }
  return h;
}

function tableWrapScore(rows, colWs, fs) {
  let extra = 0;
  for (const row of rows || []) {
    if (tableRowIsImage(row)) continue;
    for (const cell of row) {
      const u = inkUnits(visibleInk(cell.text || ""));
      if (!(u > 0) || u > 14) continue;
      extra += Math.max(0, tableCellWrapLines(cell.text, tableCellWidth(cell, colWs), fs) - 1);
    }
  }
  return extra;
}

function tableFitsBox(box, rows, colWs, fs, pad) {
  if (!(fs >= INK_FS_MIN) || !box) return false;
  for (const row of rows || []) {
    if (tableRowIsImage(row)) continue;
    for (const cell of row) {
      if (!tableCellFitsWidth(cell.text, tableCellInnerW(tableCellWidth(cell, colWs), 1), fs)) return false;
    }
  }
  return tableNeededHeight(rows, colWs, fs, pad) <= box.h + 1e-6;
}

function tableFontFit(box, html) {
  const rows = tableCells(html);
  if (!box || !rows.length) return null;
  const colWs = tableColWidths(rows, box.w);
  let best = null;
  for (const pad of [INK_TABLE_PAD, 0]) {
    let maxFit = 0;
    for (let fs = Math.round(INK_TABLE_LINE); fs >= INK_FS_MIN; fs -= 1) {
      if (!tableFitsBox(box, rows, colWs, fs, pad)) continue;
      if (!maxFit) maxFit = fs;
      if (maxFit >= 8 && fs < 8) continue;
      const wraps = tableWrapScore(rows, colWs, fs);
      if (!best || wraps < best.wraps || (wraps === best.wraps && fs > best.fs)) {
        best = { fs, wraps };
      }
    }
    if (maxFit) break;
  }
  return best ? best.fs : INK_FS_MIN;
}

function tableRowIsImage(row) {
  const cells = row || [];
  if (!cells.some((cell) => cell.hasImg)) return false;
  return cells.every((cell) => (
    cell.hasImg || String(cell.text || "").replace(/\s/g, "").length <= 4
  ));
}

function tableFontFromCells(cells, rowsMeta) {
  const heights = rowsMeta.map((row) => Number(row && row.h) || 0).filter((h) => h > 0);
  const mid = heights.length
    ? [...heights].sort((a, b) => a - b)[Math.floor(heights.length / 2)]
    : 0;
  const votes = [];
  const byRow = new Map();
  for (const cell of cells) {
    const row = rowsMeta[cell.row];
    if (!row) continue;
    const w = Number(cell.w) || 0;
    const n = Math.max(tableLineCount(cell), tableCellWrapLines(cell.text || "", w));
    const cur = byRow.get(cell.row) || { h: Number(row.h) || 0, n: 1 };
    cur.n = Math.max(cur.n, n);
    byRow.set(cell.row, cur);
  }
  for (const { h, n } of byRow.values()) {
    if (!(h > 0) || !(n >= 1)) continue;
    if (mid && h > mid * 1.8) continue;
    const vote = heightVote(h / n, false, INK_TABLE_PAD);
    if (vote != null) votes.push(vote);
  }
  return votes.length ? modeOf(votes) : null;
}

function tableFontOf(blk) {
  const box = boxOf(blk.bbox);
  const fitted = box ? tableFontFit(box, blk.table_html || "") : null;
  const cells = Array.isArray(blk.cells) ? blk.cells : [];
  const rowsMeta = Array.isArray(blk.rows) ? blk.rows : [];
  if (cells.length && rowsMeta.length) {
    const fromCells = tableFontFromCells(cells, rowsMeta);
    if (fromCells != null && fitted != null) return Math.min(fromCells, fitted);
    if (fromCells != null) return fromCells;
  }
  return fitted;
}

function tableRowWrapLines(row, colW) {
  let n = 1;
  for (const cell of row || []) {
    const w = colW * Math.max(1, Number(cell.span) || 1);
    n = Math.max(n, tableCellWrapLines(cell.text, w));
  }
  return n;
}

function round2(n) {
  return Math.round(Number(n) * 100) / 100;
}

function rowText(row) {
  return (row.spans || []).map((span) => String(span.c || "")).join("");
}

function rowBox(row) {
  const box = boxOf(row.bbox);
  if (box) return box;
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const span of row.spans || []) {
    const sb = boxOf(span.bbox);
    if (!sb) continue;
    x0 = Math.min(x0, sb.x);
    y0 = Math.min(y0, sb.y);
    x1 = Math.max(x1, sb.x + sb.w);
    y1 = Math.max(y1, sb.y + sb.h);
  }
  if (!(x1 > x0) || !(y1 > y0)) return null;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

function boxToBBox(box) {
  return [box.x, box.y, box.x + box.w, box.y + box.h];
}

function contentLineCount(lines) {
  let n = 0;
  for (const row of lineRows(lines)) {
    const has = (row.spans || []).some((span) => (
      span && (span.c || span.latex || span.t === "img" || span.t === "eq" || span.t === "ieq" || span.t === "tab")
    ));
    if (has) n += 1;
  }
  return n;
}

function printedLineCount(lines) {
  const boxed = opticalLineBoxes(lines).length;
  if (boxed >= 1) return boxed;
  return contentLineCount(lines);
}

export function printedLinesForPage(page) {
  const printed = {};
  for (const blk of page?.blocks || []) {
    const bid = String(blk?.id || "");
    if (!bid) continue;
    const n = printedLineCount(blk.lines);
    if (n >= 1) printed[bid] = n;
    const caps = Array.isArray(blk.captions) ? blk.captions : [];
    caps.forEach((cap, i) => {
      if (!cap || typeof cap !== "object") return;
      const cn = printedLineCount(cap.lines);
      if (cn >= 1) printed[`${bid}-cap${i}`] = cn;
    });
  }
  return printed;
}

function resolvePagePrinted(page) {
  const stored = page && page.printed_lines;
  if (stored && typeof stored === "object" && !Array.isArray(stored) && Object.keys(stored).length) {
    return stored;
  }
  return printedLinesForPage(page);
}

function pushVote(byType, key, vote) {
  if (vote == null || !key) return;
  (byType[key] ||= []).push(vote);
}

function voteTable(blk, byType) {
  pushVote(byType, "table-cell", tableFontOf(blk));
}

function voteHeights(hs, key, byType) {
  const title = isTitleKey(key);
  const pad = padForKey(key);
  let n = 0;
  for (const h of hs) {
    const before = (byType[key] || []).length;
    pushVote(byType, key, heightVote(h, title, pad));
    if ((byType[key] || []).length > before) n += 1;
  }
  return n;
}

function isCaptionKey(key) {
  return String(key || "").startsWith("caption");
}

function estimateSlotRows(box, text, indent) {
  if (!box) return 1;
  const n = wrapLineCount(text || "", box.w, 11, indent);
  const maxByH = Math.max(1, Math.floor(box.h / INK_H_MIN));
  return Math.max(1, Math.min(n, maxByH));
}

function slotIsTall(box, rows) {
  if (box && box.h > INK_H_MAX) return true;
  return rows.length === 1 && rows[0].h > INK_H_MAX;
}

function printedSlotRows(box, lines, text, indent, key) {
  const rows = opticalLineBoxes(lines);
  if (rows.length >= 2) return rows.length;
  if (!isTitleKey(key) && slotIsTall(box, rows)) {
    const n = estimateSlotRows(box, text, indent);
    if (n >= 2) return n;
  }
  return rows.length;
}

function lineHeightSamples(blk, key) {
  const title = isTitleKey(key);
  const cap = title ? TITLE_BAND_H : INK_H_MAX;
  const box = boxOf(blk && blk.bbox);
  const rows = opticalLineBoxes(blk && blk.lines);
  if (!title) {
    if (rows.length >= 2) return rows.map((row) => row.h);
    const text = blockText(blk);
    const indent = indentFromAttrs(blk);
    const skipCaptionFold = isCaptionKey(key) && box && box.h <= INK_H_MAX;
    if (!skipCaptionFold) {
      const n = printedSlotRows(box, blk && blk.lines, text, indent, key);
      if (n >= 2 && box) return Array.from({ length: n }, () => box.h / n);
    }
  }
  const hs = [];
  for (const row of lineRows(blk && blk.lines)) {
    const rb = rowBox(row);
    if (isPrintBox(rb) && rb.h <= cap) hs.push(rb.h);
  }
  if (hs.length) return hs;
  const stored = Number(blk && blk.line_height || 0);
  if (stored > 0 && stored <= cap) return [stored];
  if (box && box.h <= cap) return [box.h];
  return [];
}

function captionFontOf(blk) {
  const box = boxOf(blk && blk.bbox);
  if (!box) return null;
  const key = fontKey(inkClass(blk));
  if (!isCaptionKey(key)) return null;
  if (box.h <= INK_H_MAX) {
    return heightVote(box.h, false, INK_CAPTION_PAD);
  }
  const text = blockText(blk);
  const indent = indentFromAttrs(blk);
  const printed = printedSlotRows(box, blk.lines, text, indent, key);
  const n = Math.max(1, printed >= 1 ? printed : estimateSlotRows(box, text, indent));
  return heightVote(box.h / n, false, INK_CAPTION_PAD);
}

function voteBlockLines(blk, cls, byType) {
  const key = fontKey(cls);
  voteHeights(lineHeightSamples(blk, key), key, byType);
}

function collectVotes(pages) {
  const byType = {};
  for (const page of pages || []) {
    for (const item of chromeInkItems(page?.chrome)) {
      voteBlockLines({
        text: item.text,
        bbox: boxToBBox(item.box),
        lines: [],
      }, item.cls, byType);
    }
    for (const item of captionInkItems(page)) {
      voteBlockLines({
        text: item.text,
        bbox: boxToBBox(item.box),
        lines: [],
      }, item.cls, byType);
    }
    for (const blk of page?.blocks || []) {
      if (isCloseId(blk.id)) continue;
      if (blk.type === "image" || blk.type === "chart" || blk.kind === "figure") continue;
      if (blk.type === "table") {
        voteTable(blk, byType);
        continue;
      }
      if (blk.type === "interline_equation") continue;
      voteBlockLines(blk, inkClass(blk), byType);
    }
  }
  return byType;
}

function finishFontVote(byType) {
  const fonts = {};
  for (const [key, votes] of Object.entries(byType)) {
    const mode = modeOf(votes);
    if (mode != null) fonts[key] = mode;
  }
  if (fonts.body) fonts["eq-display"] = fonts.body;
  else delete fonts["eq-display"];
  return fonts;
}

export function voteFonts(pages) {
  return finishFontVote(collectVotes(pages));
}

function leadVote(boxH, nRows) {
  const h = Number(boxH) || 0;
  const n = Math.round(Number(nRows) || 0);
  if (n < 2 || !(h >= INK_H_MIN)) return null;
  const lead = Math.round(h / n);
  if (lead < 8 || lead > 24) return null;
  return lead;
}

function opticalLineBoxes(lines) {
  const boxes = [];
  for (const row of lineRows(lines)) {
    const box = rowBox(row);
    if (!isPrintBox(box)) continue;
    boxes.push(box);
  }
  boxes.sort((a, b) => a.y - b.y);
  return boxes;
}

function leadClass(blk) {
  const cls = inkClass(blk);
  return cls === "example" || cls === "eq" ? "body" : fontKey(cls);
}

function leadFromBoxLines(box, lines, text, indent, key) {
  const n = printedSlotRows(box, lines, text, indent, key);
  const fromBox = leadVote(box && box.h, n);
  if (fromBox != null) return fromBox;
  const rows = opticalLineBoxes(lines);
  if (rows.length < 2) return null;
  const deltas = [];
  for (let i = 0; i < rows.length - 1; i += 1) {
    const d = Math.round(rows[i + 1].y - rows[i].y);
    if (d >= 8 && d <= 24) deltas.push(d);
  }
  return modeOf(deltas);
}

export function voteLeads(pages) {
  const byType = {};
  for (const page of pages || []) {
    for (const item of captionInkItems(page)) {
      pushVote(byType, fontKey(item.cls), leadFromBoxLines(item.box, [], item.text, 0, fontKey(item.cls)));
    }
    for (const blk of page.blocks || []) {
      if (isCloseId(blk.id)) continue;
      if (blk.type === "image" || blk.type === "chart" || blk.type === "table" || blk.type === "interline_equation") continue;
      if (blk.kind === "figure") continue;
      const box = boxOf(blk.bbox);
      if (!box) continue;
      pushVote(byType, leadClass(blk), leadFromBoxLines(
        box,
        blk.lines,
        blockText(blk),
        indentFromAttrs(blk),
        leadClass(blk),
      ));
    }
  }
  const leads = {};
  for (const [key, votes] of Object.entries(byType)) {
    const mode = modeOf(votes);
    if (mode != null) leads[key] = mode;
  }
  return leads;
}

export function resolveFonts(page) {
  const stored = page && page.fonts;
  if (stored && typeof stored === "object" && !Array.isArray(stored)) {
    const raw = stored.fonts && typeof stored.fonts === "object" ? stored.fonts : stored;
    const out = {};
    for (const [k, v] of Object.entries(raw)) {
      if (k === "schema" || k === "ink_lh" || k === "ink_pad" || k === "ink_eq_pad" || k === "fonts") continue;
      const n = Number(v);
      if (Number.isFinite(n) && n > 0) out[k] = Math.round(n);
    }
    if (Object.keys(out).length) return out;
  }
  if (Array.isArray(page && page.book_pages) && page.book_pages.length) {
    return voteFonts(page.book_pages);
  }
  return voteFonts([page]);
}

export function resolveLeads(page) {
  const stored = page && page.leads;
  if (stored && typeof stored === "object" && !Array.isArray(stored)) {
    const raw = stored.leads && typeof stored.leads === "object" ? stored.leads : stored;
    const out = {};
    for (const [k, v] of Object.entries(raw)) {
      if (k === "schema" || k === "leads") continue;
      const n = Number(v);
      if (Number.isFinite(n) && n > 0) out[k] = Math.round(n);
    }
    if (Object.keys(out).length) return out;
  }
  if (Array.isArray(page && page.book_pages) && page.book_pages.length) {
    return voteLeads(page.book_pages);
  }
  return voteLeads([page]);
}

function lookupFont(fonts, cls) {
  const key = fontKey(cls);
  const n = Number(fonts[key]);
  if (Number.isFinite(n) && n > 0) return n;
  const body = Number(fonts.body) || 11;
  return Math.max(INK_FS_MIN, body);
}

function lookupLead(leads, cls, fs) {
  const key = fontKey(cls);
  const n = Number(leads && leads[key]);
  if (Number.isFinite(n) && n > 0) return n;
  return round2(fs * INK_LH);
}

function eqHasDisplayStruct(tex) {
  const src = String(tex || "");
  if (!src) return false;
  if (/\\(frac|dfrac|sqrt)\b/.test(src)) return true;
  if (/\\begin\{(array|matrix|pmatrix|bmatrix|vmatrix|cases|aligned)\}/.test(src)) return true;
  return /(?:^|[^\\])\\\\/.test(src);
}

function eqUseDisplay(box, tex) {
  return Boolean(box && box.h > INK_H_MAX && eqHasDisplayStruct(tex));
}

function fontForEq(blk, fonts) {
  const box = boxOf(blk && blk.bbox);
  const fs = lookupFont(fonts, "body");
  return { fs, lineH: box ? box.h : round2(fs * INK_LH), cls: "eq" };
}

function fontForBlock(blk, fonts, leads) {
  const cls = inkClass(blk);
  const paintCls = cls === "example" || cls === "eq" ? "body" : cls;
  const fs0 = lookupFont(fonts, paintCls);
  if (blk.type === "table") {
    const local = tableFontOf(blk);
    const fs = local != null ? local : fs0;
    return { fs, lineH: round2(fs * INK_TABLE_CELL_LH), cls };
  }
  const lineH0 = lookupLead(leads, paintCls, fs0);
  if (isCaptionKey(fontKey(cls))) {
    const local = captionFontOf(blk);
    const fs = local != null ? local : fs0;
    return { fs, lineH: lineH0, cls };
  }
  return { fs: fs0, lineH: lineH0, cls };
}

function paintLead(sized) {
  if (sized && sized.cls === "note") return INK_LEAD;
  return sized ? sized.lineH : 0;
}

function noteFitsBox(box, text, fs) {
  if (!(fs >= INK_FS_MIN) || !box) return false;
  const wraps = wrapLineCount(text, box.w, fs);
  const lh = round2(fs * INK_LH);
  return wraps * lh <= Number(box.h) + 1e-6;
}

function noteFontOf(item) {
  const box = item && item.box;
  if (!box) return null;
  const text = String(item.text || "").trim();
  if (!text) return null;
  if (box.h <= INK_H_MAX) {
    let fs = heightVote(box.h, false, INK_CAPTION_PAD);
    if (fs == null) return null;
    while (fs > INK_FS_MIN && wrapLineCount(text, box.w, fs) > 1) fs -= 1;
    return fs;
  }
  const n = Math.max(1, estimateSlotRows(box, text, 0));
  let fs = heightVote(box.h / n, false, INK_CAPTION_PAD);
  if (fs == null) fs = INK_FS_MIN;
  while (fs > INK_FS_MIN && !noteFitsBox(box, text, fs)) fs -= 1;
  return fs;
}

function pageNoteFont(items) {
  let fs = null;
  for (const item of items || []) {
    if ((item && item.cls) !== "note") continue;
    const local = noteFontOf(item);
    if (local == null) continue;
    fs = fs == null ? local : Math.min(fs, local);
  }
  if (fs == null) return null;
  return Math.max(INK_FS_MIN, fs - 1);
}

function fontForChrome(item, fonts, leads, pageNoteFs) {
  const fs0 = lookupFont(fonts, item.cls);
  if (item.cls === "note") {
    const fs = pageNoteFs != null ? pageNoteFs : (noteFontOf(item) ?? fs0);
    return { fs, lineH: INK_LEAD, cls: item.cls };
  }
  return { fs: fs0, lineH: lookupLead(leads, item.cls, fs0), cls: item.cls };
}

const INDENT_TOL = 2;

function indentFromAttrs(blk) {
  const stored = Number(blk.first_indent || 0);
  if (stored > 0) return Math.round(stored * 100) / 100;
  const box = boxOf(blk.bbox);
  if (!box) return 0;
  const rows = [];
  for (const row of lineRows(blk.lines)) {
    const rb = rowBox(row);
    if (rb) rows.push(rb);
  }
  if (rows.length < 2) return 0;
  const same = rows.every((rb) => (
    Math.abs(rb.w - box.w) <= INDENT_TOL && Math.abs(rb.x - box.x) <= INDENT_TOL
  ));
  if (same) return 0;
  let indent = 0;
  for (const rb of rows) {
    const dx = rb.x - box.x;
    const dw = box.w - rb.w;
    if (dx > INDENT_TOL && dw > INDENT_TOL) indent = Math.max(indent, dx);
  }
  return Math.round(indent * 100) / 100;
}

function chromeInkItems(chrome) {
  const out = [];
  if (!chrome || typeof chrome !== "object") return out;
  for (const key of ["headers", "footers", "page_numbers", "footnotes"]) {
    const list = chrome[key] || [];
    for (let i = 0; i < list.length; i++) {
      const item = list[i];
      if (!item || typeof item !== "object") continue;
      const box = boxOf(item.bbox);
      const text = chromeText(item);
      if (!box || !text) continue;
      out.push({
        box,
        text,
        cls: chromeClass(key, item),
        id: `chrome:${key}:${i}`,
        html: `<span class="kb-ir-ink-chrome">${escapeHtml(text)}</span>`,
      });
    }
  }
  return out;
}

function captionInkItems(page) {
  const out = [];
  for (const blk of page?.blocks || []) {
    const caps = Array.isArray(blk && blk.captions) ? blk.captions : [];
    for (let i = 0; i < caps.length; i++) {
      const cap = caps[i];
      if (!cap || typeof cap !== "object") continue;
      const box = boxOf(cap.bbox);
      const text = String(cap.text || "").trim();
      if (!box || !text) continue;
      const type = String(cap.type || "image_caption");
      out.push({
        box,
        text,
        cls: captionClass(type, text),
        type,
        html: escapeHtml(text),
        id: `${blk.id || "fig"}-cap${i}`,
      });
    }
  }
  return out;
}

function renderInkSheet(page, bookId) {
  const size = pageSizeOf(page);
  if (!size) return "";
  const { pageW, pageH } = size;
  const fonts = resolveFonts(page);
  const leads = resolveLeads(page);
  const printed = resolvePagePrinted(page);
  const items = [];
  for (const blk of page?.blocks || []) {
    const box = boxOf(blk && blk.bbox);
    if (!box) continue;
    const original = Math.round(Number(printed && printed[blk.id]) || 0);
    const graphic = isInkGraphic(blk);
    const sized = blk.type === "interline_equation"
      ? fontForEq(blk, fonts)
      : graphic
        ? { fs: 0, lineH: 0, cls: "figure" }
        : fontForBlock(blk, fonts, leads);
    const indent = graphic && blk.type !== "interline_equation" ? 0 : indentFromAttrs(blk);
    const calculated = graphic ? 0 : wrapLineCount(blockText(blk), box.w, sized.fs, indent);
    const foldN = graphic ? 0 : foldLockN(calculated, original, box.h, sized.lineH);
    const fold = foldN >= 1;
    const inner = renderOne(blk, bookId, "", {
      fillSlot: true,
      sheet: true,
      lockLines: fold,
      lockN: foldN,
      boxW: box.w,
      indent,
      fs: sized.fs,
    });
    if (!inner) continue;
    const lineN = foldN;
    const lineAttr = lineN >= 1 ? ` data-kb-lines="${lineN}"` : "";
    items.push(
      `<div class="kb-ir-ink" data-kb-ink="${attr(sized.cls)}"${blockAttr(blk.id)}${lineAttr} style="${inkStyle(box, pageW, pageH, sized.fs, indent, paintLead(sized))}">${inner}</div>`,
    );
  }
  for (const item of captionInkItems(page)) {
    const fake = {
      type: item.type,
      text: item.text,
      bbox: [item.box.x, item.box.y, item.box.x + item.box.w, item.box.y + item.box.h],
    };
    const capOrig = Math.round(Number(printed && printed[item.id]) || 0);
    const sized = fontForBlock(fake, fonts, leads);
    const indent = indentFromAttrs(fake);
    const calculated = wrapLineCount(item.text, item.box.w, sized.fs, indent);
    const oneLine = item.box.h <= INK_H_MAX;
    const foldN = oneLine ? 1 : foldLockN(calculated, capOrig, item.box.h, sized.lineH);
    const fold = foldN >= 1;
    const capAttr = fold ? ` data-kb-lines="${foldN}"` : "";
    const capHtml = fold
      ? foldWrappedHtml([{ t: "text", c: item.text }], "", {
        lockLines: true, lockN: foldN, boxW: item.box.w, indent, fs: sized.fs,
      }) || `<span class="kb-ir-line">${item.html}</span>`
      : `<p>${item.html}</p>`;
    items.push(
      `<div class="kb-ir-ink" data-kb-ink="${attr(sized.cls)}"${blockAttr(item.id)}${capAttr} style="${inkStyle(item.box, pageW, pageH, sized.fs, indent, paintLead(sized))}">${capHtml}</div>`,
    );
  }
  if (!items.length) return "";
  const chromeItems = chromeInkItems(page?.chrome);
  const noteFs = pageNoteFont(chromeItems);
  for (const item of chromeItems) {
    const chromeOrig = Math.round(Number(printed && printed[item.id]) || 0);
    const sized = fontForChrome(item, fonts, leads, noteFs);
    if (item.cls === "note") {
      items.push(
        `<div class="kb-ir-ink kb-ir-ink--chrome" data-kb-ink="note"${blockAttr(item.id)} style="${inkStyle(item.box, pageW, pageH, sized.fs, 0, paintLead(sized))}"><span class="kb-ir-ink-chrome">${escapeHtml(item.text)}</span></div>`,
      );
      continue;
    }
    const calculated = wrapLineCount(item.text, item.box.w, sized.fs);
    const foldN = foldLockN(calculated, chromeOrig, item.box.h, sized.lineH);
    const fold = foldN >= 1;
    const chromeAttr = fold ? ` data-kb-lines="${foldN}"` : "";
    const chromeHtml = fold
      ? foldWrappedHtml([{ t: "text", c: item.text }], "", {
        lockLines: true, lockN: foldN, boxW: item.box.w, fs: sized.fs,
      }) || `<span class="kb-ir-ink-chrome kb-ir-line">${escapeHtml(item.text)}</span>`
      : item.html;
    items.push(
      `<div class="kb-ir-ink kb-ir-ink--chrome" data-kb-ink="${attr(item.cls)}"${blockAttr(item.id)}${chromeAttr} style="${inkStyle(item.box, pageW, pageH, sized.fs, 0, paintLead(sized))}">${chromeHtml}</div>`,
    );
  }
  if (!items.length) return "";
  const sheetBits = [`--kb-pw:${pageW}`, `--kb-ph:${pageH}`];
  return `<div class="kb-ir-sheet" style="${sheetBits.join(";")}">${items.join("")}</div>`;
}

function fr3(n) {
  return Math.max(0.01, Math.round((Number(n) || 0) * 1000) / 1000);
}

function isPlateSlot(slot) {
  return /^fig_/.test(String(slot || ""));
}

function regionCols(node, pageW) {
  const kids = Array.isArray(node?.children) ? node.children : [];
  if (kids.length && pageW > 1) {
    const widths = kids.map((c) => Math.max(8, Number(c.x1) - Number(c.x0) || pageW / kids.length));
    const total = widths.reduce((a, b) => a + b, 0) || 1;
    return widths.map((w) => `${fr3(w / total)}fr`).join(" ");
  }
  const n = Math.max(2, Number(node?.slots?.length) || 2);
  return Array.from({ length: n }, () => "1fr").join(" ");
}

function blocksOf(node, blocks) {
  const idx = Array.isArray(node?.members) ? node.members : [];
  return idx.map((i) => blocks[i]).filter(Boolean);
}

// 大盒子里的空白段：按原高度折成几行字高，排出来才跟原书一样空那么多
function blankGap(node, em) {
  const h = Number(node?.y1) - Number(node?.y0);
  if (!(h > 0) || !(em > 0)) return "";
  const lines = Math.round((h / em) * 100) / 100;
  if (lines < 0.5) return "";
  return `<div class="kb-ir-blank" style="height:${lines}em"></div>`;
}

function emptyCol(node, em, plate = false) {
  const plateCls = plate ? " kb-ir-col--plate" : "";
  return `<div class="kb-ir-col kb-ir-col--empty${plateCls}">${blankGap(node, em)}</div>`;
}

function renderRegion(node, blocks, bookId, pageW, inRegion = false, em = 0, inRows = false) {
  if (!node || typeof node !== "object") return "";
  const kind = String(node.kind || "flow");
  if (kind === "rows") {
    // 横带各自独立成行，不在同一个栏区里；格子内部的上下段才算同一摞
    const stacked = inRegion || inRows;
    return (node.children || [])
      .map((c) => renderRegion(c, blocks, bookId, pageW, false, em, stacked))
      .join("");
  }
  const kids = Array.isArray(node.children) ? node.children : [];
  if (!kids.length) {
    const mine = blocksOf(node, blocks);
    if (!mine.length) {
      // 格子里摞着的空白段照原高度留白；旁边整栏空着也按大块高度占位；整带空着是页边，不画
      if (node.empty && inRows) return blankGap(node, em);
      return node.empty && inRegion ? emptyCol(node, em) : "";
    }
    return `<div class="kb-ir-col">${renderStream(mine, bookId)}</div>`;
  }
  const inner = kids.map((child) => {
    const nested = Array.isArray(child.children) && child.children.length
      && String(child.kind || "flow") !== "flow";
    if (nested) {
      return renderRegion(child, blocks, bookId, pageW, true, em, inRows);
    }
    const slot = Array.isArray(child.slots) && child.slots[0] ? child.slots[0] : "main";
    const plate = isPlateSlot(slot);
    const mine = blocksOf(child, blocks);
    const empty = Boolean(child.empty) || mine.length === 0;
    if (empty && inRows && kids.length === 1) return blankGap(child, em);
    if (empty) return emptyCol(child, em, plate);
    const plateCls = plate ? " kb-ir-col--plate" : "";
    const body = renderStream(mine, bookId, { fillSlot: plate });
    return `<div class="kb-ir-col${plateCls}">${body}</div>`;
  });
  const cls = `kb-ir-region kb-ir-region--${kind}`;
  return `<div class="${cls}" style="--kb-page-cols:${regionCols(node, pageW)}">${inner.join("")}</div>`;
}

function emOf(page) {
  const em = Number(page?.layout?.em);
  return em > 1 ? em : 0;
}

function renderBands(page, bookId) {
  const tree = page?.layout?.tree;
  const blocks = Array.isArray(page?.blocks) ? page.blocks : [];
  const pageW = pageWidthOf(page);
  if (tree && typeof tree === "object") {
    return renderRegion(tree, blocks, bookId, pageW, false, emOf(page));
  }
  return renderStream(blocks, bookId);
}

export function renderIrPage(page, bookId) {
  const opts = bookId && typeof bookId === "object" && !Array.isArray(bookId)
    ? bookId
    : { bookId: String(bookId || "") };
  const packed = page && typeof page === "object" ? { ...page } : {};
  const catalog = printTocPage(opts.printToc, packed.page);
  if (catalog) return renderPrintTocPage(catalog);
  if (Array.isArray(opts.bookPages) && opts.bookPages.length) packed.book_pages = opts.bookPages;
  const id = String(opts.bookId || opts.book_id || "");
  const sheet = renderInkSheet(packed, id);
  if (sheet) {
    return `<div class="kb-ir-page kb-ir-page--sheet">${sheet}</div>`;
  }
  const body = renderBands(packed, id);
  const folio = String(packed?.folio || packed?.layout?.folio || "");
  return `<div class="kb-ir-page">${chromeHead(packed?.chrome)}${body}${chromeFoot(packed?.chrome, folio)}</div>`;
}
