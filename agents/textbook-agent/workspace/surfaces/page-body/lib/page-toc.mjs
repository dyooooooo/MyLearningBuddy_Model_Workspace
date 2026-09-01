const TRAILING_PAGE = /^(.+?)\s+(\d{1,4})$/;
const CHAPTER = /^[第][一二三四五六七八九十百零\d]+章/;
const PART = /^[第][一二三四五六七八九十百零\d]+部分/;
const SECTION = /^\*?\d+\.\d+/;
const APPENDIX_ITEM = /^附录\s*\d+/;
const APPENDIX_HEAD = /^附录$|^Appendices$/i;
const INTRO = /^引论(?:\s|$)/;
const LATIN_UNIT_NUM = /^\d+\s+[A-Z][A-Za-z]/;
const LATIN_GROUP_BARE = /^[A-Z][A-Za-z]+(?:[ \-][A-Za-z]+){0,4}$/;
const DEFAULT_TITLES = ["目录", "目 录", "CONTENTS", "Contents"];
const HEADING_TAGS = new Set(["div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "span"]);

function titleSet(profile) {
  const raw = profile.toc_titles;
  const list = Array.isArray(raw) && raw.length ? raw : DEFAULT_TITLES;
  return new Set(list.map((t) => String(t)));
}

export function asTocTitle(line, profile = {}) {
  const titles = titleSet(profile);
  const t = (line || "").trim().replace(/^#{1,6}\s+/, "");
  const compact = t.replace(/[\s\u3000]/g, "");
  if (compact === "目录") return "目录";
  if (compact.toLowerCase() === "contents") return "Contents";
  if (titles.has(t)) return t;
  return "";
}

function isSelfHeading(label) {
  return /^self-?assessment$/i.test((label || "").replace(/\s+/g, ""));
}

function isGroupHeading(label, printPage = null) {
  const t = (label || "").trim();
  if (APPENDIX_ITEM.test(t)) return false;
  if (
    CHAPTER.test(t) ||
    PART.test(t) ||
    APPENDIX_HEAD.test(t) ||
    INTRO.test(t) ||
    LATIN_UNIT_NUM.test(t)
  ) {
    return true;
  }
  if (printPage != null || isSelfHeading(t) || asTocTitle(t)) return false;
  return LATIN_GROUP_BARE.test(t);
}

function levelOf(label, printPage = null) {
  if (APPENDIX_ITEM.test(label)) return 1;
  if (isGroupHeading(label, printPage)) return 0;
  if (/^\*?\d+\.\d+\.\d+\b/.test(label)) return 2;
  if (SECTION.test(label)) return 1;
  if (/^\*?\d+\s+\S/.test(label)) return 1;
  return 1;
}

export function parseTocLine(raw) {
  let text = (raw || "").trim().replace(/^#{1,6}\s+/, "").replace(/^[-•·* ]+/, "");
  if (text.startsWith("\\*")) text = `*${text.slice(2)}`;
  const match = TRAILING_PAGE.exec(text);
  if (match) {
    const label = match[1].replace(/[ .\u3000．…·•]+$/, "").trim();
    const page = Number(match[2]);
    if (label && !/^\d+$/.test(label)) {
      return { label, printPage: page, isChapter: isGroupHeading(label, page), level: levelOf(label, page) };
    }
  }
  return { label: text, printPage: null, isChapter: isGroupHeading(text, null), level: levelOf(text, null) };
}

function looksLikeEntry(line, profile) {
  const s = (line || "").trim();
  if (!s || asTocTitle(s, profile) || titleSet(profile).has(s)) return false;
  if (/[=\\]/.test(s) || /^(?:\(|当|解)/.test(s)) return false;
  if (isGroupHeading(s) || CHAPTER.test(s) || PART.test(s) || SECTION.test(s) || APPENDIX_ITEM.test(s)) return true;
  const ent = parseTocLine(s);
  return ent.printPage != null && Boolean(ent.label);
}

export function looksLikeTocBlob(text, profile = {}) {
  const lines = (text || "")
    .split(/\r?\n/)
    .map((ln) => ln.trim())
    .filter(Boolean);
  if (!lines.length) return false;
  const hits = lines.filter((ln) => looksLikeEntry(ln, profile));
  if (lines.length === 1) return parseTocLine(lines[0]).printPage != null;
  if (hits.length < 2) return false;
  return hits.length / lines.length >= 0.55;
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function headingTag(profile) {
  const tag = String(profile.heading_tag || "div").toLowerCase();
  return HEADING_TAGS.has(tag) ? tag : "div";
}

function rowHtml(entry, printToPdf) {
  const classes = [`kb-page-toc-row`, `kb-page-toc-row--lv${entry.level}`];
  if (entry.isChapter) classes.push("kb-page-toc-row--chapter");
  const attrs = [];
  let leader = "";
  let page = "";
  if (entry.printPage != null) {
    classes.push("kb-page-toc-row--link");
    attrs.push(`data-goto-print-page="${entry.printPage}"`);
    const mapped = Number(printToPdf[String(entry.printPage)] || 0);
    if (mapped > 0) attrs.push(`data-goto-page="${mapped}"`);
    attrs.push("role=\"link\"", "tabindex=\"0\"");
    leader = '<span class="kb-page-toc-leader" aria-hidden="true"></span>';
    page = `<span class="kb-page-toc-page">${entry.printPage}</span>`;
  }
  return (
    `<li class="${classes.join(" ")}"${attrs.length ? ` ${attrs.join(" ")}` : ""}>` +
    `<span class="kb-page-toc-title">${escapeHtml(entry.label)}</span>` +
    `${leader}${page}</li>`
  );
}

export function liftPageToc(md, printToPdf = {}, profile = {}) {
  const raw = (md || "").replace(/\r\n/g, "\n");
  if (raw.includes('class="kb-page-toc"') || !looksLikeTocBlob(raw, profile)) return raw;
  const lines = raw.split("\n");
  const rows = [];
  let heading = "";
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const titleText = asTocTitle(trimmed, profile);
    if (titleText) {
      heading = titleText;
      continue;
    }
    if (!looksLikeEntry(trimmed, profile) && !isGroupHeading(trimmed)) continue;
    rows.push(rowHtml(parseTocLine(trimmed), printToPdf));
  }
  if (rows.length < 2) return raw;
  const tag = headingTag(profile);
  const title = heading ? `<${tag} class="kb-page-toc-heading">${escapeHtml(heading)}</${tag}>` : "";
  return `${title}<nav class="kb-page-toc"><ol class="kb-page-toc-list">${rows.join("")}</ol></nav>`;
}
