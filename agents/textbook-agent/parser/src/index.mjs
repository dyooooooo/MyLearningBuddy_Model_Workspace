/**
 * Parse `::: kb-*` fences and Obsidian-flavored bits with remark, then hand
 * the original source slices back. We never stringify the AST — HTML tables,
 * math, and images stay byte-identical to the mid file.
 *
 * Our mid files write `::: kb-title` (space after the colons). micromark's
 * directive grammar wants `:::kb-title`. `adaptDialect` inserts a newline
 * when tag() glues a fence to prose, then deletes that one space on opener
 * lines. `origOf` maps adapted offsets back; inserted newlines are virtual.
 */
import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkDirective from "remark-directive";
import { remarkOfm } from "mdian";
import { visit } from "unist-util-visit";

const KB = /^kb-[\w-]+$/;
const OPENER = /^(:+) (kb-[\w-]+)([ \t]*\r?\n?)$/;
const OPENER_TOKEN = /:{3,}(?:[ \t]+kb-[\w-]+|kb-[\w-]+)/g;
const NAMED_LINE = /^:{3,}(?:[ \t]+kb-[\w-]+|kb-[\w-]+)[ \t]*$/;
const COLON_ONLY = /^:{3,}[ \t]*$/;
const CALLOUT_HEAD = /^>\s*\[!([\w-]+)\]([+-])?(?:\s+(.*))?$/;

function processor() {
  return unified().use(remarkParse).use(remarkDirective).use(remarkOfm);
}

function collectDetach(text) {
  const insertBefore = new Set();
  const insertAfter = new Set();
  const opener = new RegExp(OPENER_TOKEN.source, "g");
  let token;
  while ((token = opener.exec(text))) {
    const lineStart = token.index === 0 ? 0 : text.lastIndexOf("\n", token.index - 1) + 1;
    if (/\S/.test(text.slice(lineStart, token.index))) insertBefore.add(token.index);
    const end = token.index + token[0].length;
    if (end < text.length) {
      const next = text[end];
      if (next !== "\n" && next !== "\r" && /[^A-Za-z0-9_\s-]/.test(next)) {
        insertAfter.add(end - 1);
      }
    }
  }
  let lineStart = 0;
  while (lineStart <= text.length) {
    const nl = text.indexOf("\n", lineStart);
    const lineEnd = nl < 0 ? text.length : nl;
    const line = text.slice(lineStart, lineEnd);
    const close = /^(:{3,})([ \t]*)(.*)$/.exec(line);
    if (close && !NAMED_LINE.test(line) && close[3] && !/^kb-[\w-]+/.test(close[3])) {
      insertAfter.add(lineStart + close[1].length - 1);
    }
    const trail = /^(.*\S)([ \t]*)(:{3,})[ \t]*$/.exec(line);
    if (trail && !NAMED_LINE.test(line) && !COLON_ONLY.test(line)) {
      insertBefore.add(lineStart + trail[1].length + trail[2].length);
    }
    if (nl < 0) break;
    lineStart = nl + 1;
  }
  return { insertBefore, insertAfter };
}

function dropOpenerSpaces(adapted, origOf, original) {
  let out = "";
  const mapped = [];
  const lines = adapted.split(/(?<=\n)/);
  let offset = 0;
  for (const line of lines) {
    const match = OPENER.exec(line);
    if (match) {
      const spaceAt = offset + match[1].length;
      for (let i = 0; i < line.length; i += 1) {
        if (offset + i === spaceAt) continue;
        out += line[i];
        mapped.push(origOf[offset + i]);
      }
    } else {
      for (let i = 0; i < line.length; i += 1) {
        out += line[i];
        mapped.push(origOf[offset + i]);
      }
    }
    offset += line.length;
  }
  mapped.push(original.length);
  return { adapted: out, origOf: mapped, original };
}

export function adaptDialect(md) {
  const text = String(md || "");
  const { insertBefore, insertAfter } = collectDetach(text);
  let detached = "";
  const detachedOf = [];
  for (let i = 0; i < text.length; i += 1) {
    if (insertBefore.has(i)) {
      detached += "\n";
      detachedOf.push(i);
    }
    detached += text[i];
    detachedOf.push(i);
    if (insertAfter.has(i)) {
      detached += "\n";
      detachedOf.push(i + 1);
    }
  }
  return dropOpenerSpaces(detached, detachedOf, text);
}

function toOrig(origOf, adaptedOffset, originalLen) {
  if (adaptedOffset <= 0) return 0;
  if (adaptedOffset >= origOf.length) return originalLen;
  return origOf[adaptedOffset];
}

function offsets(node) {
  const start = node?.position?.start?.offset;
  const end = node?.position?.end?.offset;
  if (!Number.isInteger(start) || !Number.isInteger(end) || end < start) return null;
  return { start, end };
}

function innerOffsets(md, start, end) {
  const block = md.slice(start, end);
  const firstNl = block.indexOf("\n");
  const lastNl = block.lastIndexOf("\n");
  if (firstNl < 0 || lastNl <= firstNl) {
    return { innerStart: start, innerEnd: start };
  }
  return { innerStart: start + firstNl + 1, innerEnd: start + lastNl };
}

function mapFence(original, origOf, adaptedStart, adaptedEnd) {
  const start = toOrig(origOf, adaptedStart, original.length);
  const end = toOrig(origOf, adaptedEnd, original.length);
  const inner = innerOffsets(original, start, end);
  return { start, end, ...inner };
}

export function extractFences(md) {
  const text = String(md || "");
  if (!text) return [];
  const { adapted, origOf, original } = adaptDialect(text);
  const tree = processor().parse(adapted);
  const found = [];
  visit(tree, (node) => {
    if (node.type !== "containerDirective" && node.type !== "leafDirective") return;
    if (!KB.test(node.name || "")) return;
    const span = offsets(node);
    if (!span) return;
    found.push({
      name: node.name,
      ...mapFence(original, origOf, span.start, span.end),
    });
  });
  found.sort((a, b) => a.start - b.start || b.end - a.end);
  return found;
}

export function extractTopLevel(md) {
  const all = extractFences(md);
  return all.filter(
    (row, i) =>
      !all.some(
        (other, j) =>
          j !== i &&
          other.start <= row.start &&
          row.end <= other.end &&
          (other.start < row.start || row.end < other.end),
      ),
  );
}

function ofmKind(node) {
  const t = String(node.type || "");
  if (t === "wikiLink" || t === "ofmWikiLink") return "wikilink";
  if (t === "highlight" || t === "ofmHighlight") return "highlight";
  if (t === "comment" || t === "ofmComment") return "comment";
  if (t === "callout" || t === "ofmCallout") return "callout";
  return "";
}

function calloutFromBlockquote(text, node) {
  const span = offsets(node);
  if (!span) return null;
  const raw = text.slice(span.start, span.end);
  const first = (raw.split(/\r?\n/)[0] || "").replace(/^\s+/, "");
  const match = CALLOUT_HEAD.exec(first);
  if (!match) return null;
  return {
    kind: "callout",
    start: span.start,
    end: span.end,
    value: raw,
    calloutType: match[1],
    title: (match[3] || "").trim(),
    data: {},
    url: "",
    alias: "",
  };
}

export function extractOfm(md) {
  const text = String(md || "");
  if (!text) return [];
  const proc = processor();
  const tree = proc.parse(text);
  const found = [];
  visit(tree, "blockquote", (node) => {
    const hit = calloutFromBlockquote(text, node);
    if (hit) found.push(hit);
  });
  proc.runSync(tree);
  visit(tree, (node) => {
    const kind = ofmKind(node);
    if (!kind || kind === "callout") return;
    const span = offsets(node);
    if (!span) return;
    const props = node.data?.hProperties || {};
    found.push({
      kind,
      start: span.start,
      end: span.end,
      value: text.slice(span.start, span.end),
      data: node.data && typeof node.data === "object" ? node.data : {},
      url: props.dataOfmPath || node.url || node.value || "",
      alias: props.dataOfmAlias || node.alias || "",
      fragment: props.dataOfmFragment || "",
    });
  });
  found.sort((a, b) => a.start - b.start || b.end - a.end);
  return found.filter((row, i, all) => !all.some((other, j) => j !== i && other.start <= row.start && row.end <= other.end && other !== row && (other.start < row.start || row.end < other.end)));
}

export function parseDebug(md) {
  const text = String(md || "");
  const { adapted } = adaptDialect(text);
  const tree = processor().parse(adapted);
  const types = [];
  visit(tree, (node) => {
    types.push(node.type + (node.name ? `:${node.name}` : ""));
  });
  return { types, fences: extractFences(text), ofm: extractOfm(text) };
}
