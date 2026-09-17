const STRUCTURAL = new Set(["title", "table", "interline_equation", "image", "chart"]);
const STOP_KINDS = new Set(["box", "figure", "ref"]);

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

function linesHtml(lines, bookId) {
  if (!lines || !lines.length) return "";
  const bits = [];
  for (const row of lines) {
    for (const span of row || []) {
      const kind = span.t || "text";
      if (kind === "eq" || kind === "ieq") {
        const tex = (span.latex || span.c || "").trim();
        // 公式只出占位，排版归前端（kb-ui 扫 [data-latex]）；ieq 要独占一行居中
        const cls = kind === "ieq" ? "math-inline math--display" : "math-inline";
        if (tex) bits.push(`<span class="${cls}" data-latex="${attr(tex)}"></span>`);
      } else if (kind === "img" && span.c) {
        const src = irAssetUrl(bookId, span.c);
        if (src) bits.push(`<img class="kb-ir-inline" src="${attr(src)}" alt="">`);
      } else if (kind === "sub") {
        bits.push(`<sub>${escapeHtml(span.c || "")}</sub>`);
      } else if (kind === "sup") {
        bits.push(`<sup>${escapeHtml(span.c || "")}</sup>`);
      } else if (kind === "tab") {
        bits.push(`<span class="kb-ir-tab"></span>`);
      } else if (span.c) {
        bits.push(escapeHtml(span.c));
      }
    }
  }
  return bits.join("");
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

function paragraph(blk, bookId, extras = "") {
  const inner = linesHtml(blk.lines, bookId) || escapeHtml((blk.text || "").trim());
  const body = `${extras}${inner}`.trim();
  if (!body) return "";
  return `<p${classAttr((alignClass(blk) + indentClass(blk)).trim())}${blockAttr(blk.id)}>${body}</p>`;
}

function figureHtml(blk, bookId, cell = false, opts = {}) {
  const src = irAssetUrl(bookId, blk.asset || "");
  const caption = (blk.caption || "").trim();
  const sized = sizeAttrs(blk.width_frac, opts.fillSlot);
  const cls = `${cell ? "kb-ir-figcell" : "kb-ir-fig"}${sized.cls}`;
  const alt = attr(caption || "插图");
  const img = src ? `<img src="${attr(src)}" alt="${alt}">` : "";
  const cap = caption ? `<figcaption>${escapeHtml(caption)}</figcaption>` : "";
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
  const latex = (blk.latex || "").trim();
  if (latex) {
    return `<div class="kb-ir-eq"${blockAttr(blk.id)} data-latex="${attr(latex)}"></div>`;
  }
  const src = irAssetUrl(bookId, blk.asset || "");
  if (src) {
    return `<div class="kb-ir-eq"${blockAttr(blk.id)}><img src="${attr(src)}" alt="公式"></div>`;
  }
  const text = (blk.text || "").trim();
  return text ? `<div class="kb-ir-eq"${blockAttr(blk.id)}>${escapeHtml(text)}</div>` : "";
}

function titleHtml(blk) {
  const level = Math.max(1, Math.min(6, Number(blk.level) || 2));
  const text = (blk.text || "").trim();
  if (!text) return "";
  return `<h${level}${classAttr(alignClass(blk).trim())}${blockAttr(blk.id)}>${escapeHtml(text)}</h${level}>`;
}

function tableHtml(blk, bookId, opts = {}) {
  const caption = (blk.caption || "").trim();
  const html = (blk.table_html || "").trim();
  const src = irAssetUrl(bookId, blk.asset || "");
  const bits = [];
  if (html) bits.push(html);
  else if (src) bits.push(`<img src="${attr(src)}" alt="${attr(caption || "表格")}">`);
  if (!bits.length && !caption) return "";
  const cap = caption ? `<figcaption>${escapeHtml(caption)}</figcaption>` : "";
  const sized = sizeAttrs(blk.width_frac, opts.fillSlot);
  return `<figure class="kb-ir-table${sized.cls}"${sized.style}${blockAttr(blk.id)}>${bits.join("")}${cap}</figure>`;
}

function refHtml(blk, bookId) {
  const inner = linesHtml(blk.lines, bookId) || escapeHtml((blk.text || "").trim());
  if (!inner) return "";
  return `<aside class="kb-ir-ref"${blockAttr(blk.id)}>${inner}</aside>`;
}

function renderOne(blk, bookId, extras = "", opts = {}) {
  if (blk.kind === "box") return boxHtml(blk, "");
  if (blk.kind === "ref") return refHtml(blk, bookId);
  if (blk.kind === "figure" || blk.type === "image" || blk.type === "chart") {
    return figureHtml(blk, bookId, false, opts);
  }
  if (blk.type === "table") return tableHtml(blk, bookId, opts);
  if (blk.type === "interline_equation") return eqHtml(blk, bookId);
  if (blk.type === "title") return titleHtml(blk);
  return paragraph(blk, bookId, extras);
}

function isStopAfterBox(blk) {
  if (STOP_KINDS.has(blk.kind)) return true;
  if (blk.kind === "inline_image") return false;
  return STRUCTURAL.has(blk.type);
}

function renderStream(blocks, bookId, opts = {}) {
  const extras = {};
  const skip = new Set();
  for (let i = 0; i < blocks.length; i++) {
    const blk = blocks[i];
    if (blk.kind !== "inline_image") continue;
    const img = inlineImg(blk, bookId);
    if (!img) continue;
    const prevOk = i > 0 && blocks[i - 1].type === "text" && !skip.has(i - 1);
    const nextOk = i + 1 < blocks.length && blocks[i + 1].type === "text";
    if (prevOk) {
      extras[i - 1] = `${extras[i - 1] || ""}${img}`;
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
      const kids = [];
      let j = i + 1;
      while (j < blocks.length) {
        if (skip.has(j)) {
          j += 1;
          continue;
        }
        const nxt = blocks[j];
        if (nxt.row != null || isStopAfterBox(nxt)) break;
        kids.push(renderOne(nxt, bookId, extras[j] || "", opts));
        skip.add(j);
        j += 1;
      }
      parts.push(boxHtml(blk, kids.filter(Boolean).join("")));
      continue;
    }
    const html = renderOne(blk, bookId, extras[i] || "", opts);
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

function renderRegion(node, blocks, bookId, pageW, inRegion = false) {
  if (!node || typeof node !== "object") return "";
  const kind = String(node.kind || "flow");
  if (kind === "rows") {
    // 横带各自独立成行，不在同一个栏区里
    return (node.children || [])
      .map((c) => renderRegion(c, blocks, bookId, pageW, false))
      .join("");
  }
  const kids = Array.isArray(node.children) ? node.children : [];
  if (!kids.length) {
    const mine = blocksOf(node, blocks);
    if (!mine.length) {
      // 空位占位只在栏区里有意义（标出旁边那栏留白）；整带空着就是页面留白，不画
      return node.empty && inRegion ? `<div class="kb-ir-col kb-ir-col--empty"></div>` : "";
    }
    return `<div class="kb-ir-col">${renderStream(mine, bookId)}</div>`;
  }
  const inner = kids.map((child) => {
    const nested = Array.isArray(child.children) && child.children.length
      && String(child.kind || "flow") !== "flow";
    if (nested) {
      return renderRegion(child, blocks, bookId, pageW, true);
    }
    const slot = Array.isArray(child.slots) && child.slots[0] ? child.slots[0] : "main";
    const plate = isPlateSlot(slot);
    const mine = blocksOf(child, blocks);
    const empty = Boolean(child.empty) || mine.length === 0;
    const emptyCls = empty ? " kb-ir-col--empty" : "";
    const plateCls = plate ? " kb-ir-col--plate" : "";
    const body = empty ? "" : renderStream(mine, bookId, { fillSlot: plate });
    return `<div class="kb-ir-col${emptyCls}${plateCls}">${body}</div>`;
  });
  const cls = `kb-ir-region kb-ir-region--${kind}`;
  return `<div class="${cls}" style="--kb-page-cols:${regionCols(node, pageW)}">${inner.join("")}</div>`;
}

function renderBands(page, bookId) {
  const tree = page?.layout?.tree;
  const blocks = Array.isArray(page?.blocks) ? page.blocks : [];
  const pageW = pageWidthOf(page);
  if (tree && typeof tree === "object") {
    return renderRegion(tree, blocks, bookId, pageW);
  }
  return renderStream(blocks, bookId);
}

export function renderIrPage(page, bookId) {
  const body = renderBands(page, bookId);
  const folio = String(page?.layout?.folio || "");
  return `<div class="kb-ir-page">${chromeHead(page?.chrome)}${body}${chromeFoot(page?.chrome, folio)}</div>`;
}
