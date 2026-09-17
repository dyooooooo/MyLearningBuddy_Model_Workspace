// Give each figure the width it had in the printed book, and keep
// same-row plates on one line.
//
// Markdown's `![alt](url)` has no size slot, so without this every figure fell
// back to one global CSS cap and came out the same width — tiny inline glyphs
// blown up, full-page plates shrunk. `ctx.figWidths` maps an asset filename to
// its width as a percentage of the body text column, measured from the MinerU
// bounding box. `ctx.figRows` lists groups that share a horizontal band; those
// become a flex row instead of each taking its own line.
//
// Setting only `width` (never `height`) lets the browser derive the height from
// the asset, so the original aspect ratio survives untouched.

const IMAGE = /!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)/g;
const CAPTION = "(?:\\s*(?:\\*[^\\*\\n]+\\*|<em>[^<]*<\\/em>|<i>[^<]*<\\/i>))?";
const FIG_UNIT = new RegExp(
  `<div class="kb-center">\\s*(<img\\b[^>]*>${CAPTION})\\s*<\\/div>` +
    `|` +
    `<img\\b[^>]*>${CAPTION}`,
  "g",
);

function escapeAttr(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// The asset filename is the one token shared between the stored blocks and the
// rendered link, which by then is a percent-encoded `?key=` proxy URL.
export function assetName(url) {
  let s = String(url || "");
  const at = s.indexOf("key=");
  if (at >= 0) s = s.slice(at + 4).split("&")[0];
  try {
    s = decodeURIComponent(s);
  } catch {
    /* a malformed escape means the raw text is the best key we have */
  }
  return s.replace(/\\/g, "/").split("?")[0].split("/").pop() || "";
}

function srcOf(tag) {
  const m = /\bsrc="([^"]+)"/.exec(tag || "");
  return m ? m[1] : "";
}

function widthOf(tag) {
  const m = /\bdata-fig-width="([\d.]+)"/.exec(tag || "");
  const n = m ? Number(m[1]) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 0;
}

// A glyph-sized figure sitting between words is an inline symbol, not a plate:
// it must flow with the sentence instead of breaking onto its own centered
// line. One alone on its line is a small standalone figure and stays centered.
function sharesLineWithText(whole, offset, length) {
  const lineStart = whole.lastIndexOf("\n", offset - 1) + 1;
  let lineEnd = whole.indexOf("\n", offset + length);
  if (lineEnd < 0) lineEnd = whole.length;
  const before = whole.slice(lineStart, offset).trim();
  const after = whole.slice(offset + length, lineEnd).trim();
  return Boolean(before) || Boolean(after);
}

const SMALL_PCT = 12;

function applyWidths(md, widths) {
  return String(md || "").replace(IMAGE, (all, alt, url, offset, whole) => {
    const pct = Number(widths[assetName(url)]);
    if (!Number.isFinite(pct) || pct <= 0) return all;
    const names = [];
    if (pct < SMALL_PCT) {
      names.push(sharesLineWithText(whole, offset, all.length) ? "kb-fig-inline" : "kb-fig-sm");
    }
    const cls = names.length ? ` class="${names.join(" ")}"` : "";
    return (
      `<img src="${escapeAttr(url)}" alt="${escapeAttr(alt)}"` +
      `${cls} style="width:${pct}%" data-fig-width="${pct}">`
    );
  });
}

function rowLookup(figRows) {
  const rowOf = Object.create(null);
  const list = Array.isArray(figRows) ? figRows : [];
  for (const row of list) {
    if (!Array.isArray(row) || row.length < 2) continue;
    const id = row.join("\0");
    for (const name of row) {
      if (name) rowOf[name] = id;
    }
  }
  return rowOf;
}

function restyleCell(unit, pct) {
  const scaled = pct > 0 ? pct : widthOf(unit);
  const img = unit.replace(/<img\b([^>]*)>/, (_all, attrs) => {
    let a = String(attrs || "").replace(/\s*style="[^"]*"/, "");
    return `<img${a} style="width:100%">`;
  });
  const w = scaled > 0 ? scaled : 0;
  const style = w > 0 ? ` style="width:${w}%"` : "";
  const data = w > 0 ? ` data-fig-width="${w}"` : "";
  return `<figure class="kb-fig-cell"${style}${data}>${img}</figure>`;
}

function wrapRow(units) {
  const raw = units.map((u) => widthOf(u.text) || 0);
  const sum = raw.reduce((a, b) => a + b, 0);
  const scale = sum > 100 && sum > 0 ? 100 / sum : 1;
  const cells = units.map((u, i) => restyleCell(u.text, raw[i] * scale));
  return `<div class="kb-fig-row">\n\n${cells.join("\n\n")}\n\n</div>`;
}

export function groupRowImages(md, ctx = {}) {
  const rowOf = rowLookup(ctx && ctx.figRows);
  if (!Object.keys(rowOf).length) return md || "";
  const text = String(md || "");
  const tokens = [];
  let last = 0;
  FIG_UNIT.lastIndex = 0;
  let m;
  while ((m = FIG_UNIT.exec(text))) {
    if (m.index > last) tokens.push({ kind: "text", text: text.slice(last, m.index) });
    const inner = m[1] || m[0];
    const name = assetName(srcOf(inner));
    const inline = /\bkb-fig-inline\b/.test(inner);
    tokens.push({
      kind: "fig",
      text: inner,
      raw: m[0],
      name,
      inline,
      row: inline ? "" : rowOf[name] || "",
    });
    last = m.index + m[0].length;
  }
  if (last < text.length) tokens.push({ kind: "text", text: text.slice(last) });

  const out = [];
  let i = 0;
  while (i < tokens.length) {
    const tok = tokens[i];
    if (tok.kind !== "fig" || !tok.row) {
      out.push(tok.raw || tok.text);
      i += 1;
      continue;
    }
    const group = [tok];
    let j = i + 1;
    while (j < tokens.length) {
      const nxt = tokens[j];
      if (nxt.kind === "text" && !String(nxt.text).replace(/\s+/g, "")) {
        j += 1;
        continue;
      }
      if (nxt.kind === "fig" && nxt.row === tok.row) {
        group.push(nxt);
        j += 1;
        continue;
      }
      break;
    }
    if (group.length >= 2) {
      out.push(wrapRow(group));
      i = j;
    } else {
      out.push(tok.raw || tok.text);
      i += 1;
    }
  }
  return out.join("");
}

export function sizeImages(md, ctx = {}) {
  const widths = ctx && typeof ctx.figWidths === "object" && ctx.figWidths ? ctx.figWidths : {};
  const sized = Object.keys(widths).length ? applyWidths(md, widths) : md || "";
  return groupRowImages(sized, ctx);
}
