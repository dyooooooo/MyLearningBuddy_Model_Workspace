const TRAILING_PAGE = /^(.+?)\s+(\d{1,4})$/;
const SLASH_PAGE = /^(.+?)\s*\/\s*(\d{1,4})$/;
const CHAPTER = /^[第][一二三四五六七八九十百零\d]+章/;
const PART = /^[第][一二三四五六七八九十百零\d]+部分/;
const SECTION_JIE = /^第[一二三四五六七八九十百零\d]+节/;
const SECTION = /^\*?\d+\.\d+/;
const APPENDIX_ITEM = /^附录\s*\d+/;
const APPENDIX_HEAD = /^附录$|^Appendices$/i;
const INTRO = /^引论(?:\s|$)/;
const LATIN_UNIT_NUM = /^\d+\s+[A-Z][A-Za-z]/;
const LATIN_GROUP_BARE = /^[A-Z][A-Za-z]+(?:[ \-][A-Za-z]+){0,4}$/;
const CN_ENUM_CHAPTER = /^[一二三四五六七八九十]+、/;
const CN_ENUM_SECTION = /^[（(][一二三四五六七八九十]+[）)]/;
const UNIT = /^第[一二三四五六七八九十百零〇两\d]+单元/;
const LESSON_ONLY = /^\d{1,2}$/;
const UNIT_TASK = /^单元学习任务$/;
const BACK_MATTER = /^后记$/;
const CN_ACTIVITY = /^[一二三四五六七八九十]+\s+\S/;
const DEFAULT_TITLES = ["目录", "目 录", "CONTENTS", "Contents"];
const HEADING_TAGS = new Set(["div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "span"]);

function compileLexicon(lexicon) {
  if (!lexicon || typeof lexicon !== "object") return null;
  if (Array.isArray(lexicon.suffixes) && Array.isArray(lexicon.lines) && lexicon.compiled) {
    return lexicon;
  }
  const suffixes = [];
  for (const row of lexicon.page_suffix || []) {
    if (!row || !row.pattern) continue;
    try {
      suffixes.push(new RegExp(row.pattern));
    } catch {
      /* skip invalid */
    }
  }
  const lines = [];
  for (const row of lexicon.lines || []) {
    if (!row || !row.pattern) continue;
    try {
      const flags = String(row.flags || "");
      lines.push({
        id: String(row.id || ""),
        kind: String(row.kind || "section"),
        level: Number(row.level),
        re: new RegExp(row.pattern, flags),
      });
    } catch {
      /* skip invalid */
    }
  }
  if (!suffixes.length && !lines.length) return null;
  return { suffixes, lines, compiled: true };
}

function classifyLexicon(label, lexicon) {
  if (!lexicon) return null;
  for (const rule of lexicon.lines || []) {
    if (!rule.re.test(label)) continue;
    const level = Number.isFinite(rule.level) ? rule.level : 1;
    return { level, isChapter: rule.kind === "chapter" || level === 0, id: rule.id };
  }
  return null;
}

function titleSet(profile) {
  const raw = profile.toc_titles;
  const list = Array.isArray(raw) && raw.length ? raw : DEFAULT_TITLES;
  return new Set(list.map((t) => String(t)));
}

function bareLine(line) {
  return (line || "").trim().replace(/^#{1,6}\s+/, "");
}

export function asTocTitle(line, profile = {}) {
  const titles = titleSet(profile);
  const t = bareLine(line);
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
  if (APPENDIX_ITEM.test(t) || CN_ENUM_SECTION.test(t) || SECTION_JIE.test(t)) return false;
  if (
    CHAPTER.test(t) ||
    PART.test(t) ||
    APPENDIX_HEAD.test(t) ||
    INTRO.test(t) ||
    LATIN_UNIT_NUM.test(t) ||
    CN_ENUM_CHAPTER.test(t) ||
    UNIT.test(t) ||
    BACK_MATTER.test(t)
  ) {
    return true;
  }
  if (printPage != null || isSelfHeading(t) || asTocTitle(t)) return false;
  return LATIN_GROUP_BARE.test(t);
}

function levelOf(label, printPage = null) {
  if (APPENDIX_ITEM.test(label) || CN_ENUM_SECTION.test(label)) return 1;
  if (isGroupHeading(label, printPage)) return 0;
  if (/^\*?\d+\.\d+\.\d+\b/.test(label)) return 2;
  if (SECTION.test(label)) return 1;
  if (/^\*?\d+\s+\S/.test(label)) return 1;
  return 1;
}

function splitPrintPage(text, lexicon = null) {
  if (lexicon && Array.isArray(lexicon.suffixes)) {
    for (const re of lexicon.suffixes) {
      re.lastIndex = 0;
      const match = re.exec(text);
      if (!match) continue;
      const label = text.slice(0, match.index).replace(/[ .\u3000．…·•、]+$/, "").trim();
      const page = Number(match[1]);
      if (label && Number.isFinite(page)) return { label, printPage: page };
    }
  }
  const slash = SLASH_PAGE.exec(text);
  if (slash) {
    const label = slash[1].replace(/[ .\u3000．…·•、]+$/, "").trim();
    const page = Number(slash[2]);
    if (label) return { label, printPage: page };
  }
  const space = TRAILING_PAGE.exec(text);
  if (space) {
    const label = space[1].replace(/[ .\u3000．…·•、]+$/, "").trim();
    const page = Number(space[2]);
    if (label) return { label, printPage: page };
  }
  return null;
}

export function parseTocLine(raw, lexicon = null) {
  let text = bareLine(raw).replace(/^[-•·]\s+|^\*\s+/, "");
  if (text.startsWith("\\*")) text = `*${text.slice(2)}`;
  const compiled = compileLexicon(lexicon);
  const split = splitPrintPage(text, compiled);
  const label = split ? split.label : text;
  const printPage = split ? split.printPage : null;
  const hit = classifyLexicon(label, compiled);
  if (hit) {
    return { label, printPage, isChapter: hit.isChapter, level: hit.level };
  }
  return {
    label,
    printPage,
    isChapter: isGroupHeading(label, printPage),
    level: levelOf(label, printPage),
  };
}

function classifyUnitRow(ent, afterLesson) {
  const t = (ent.label || "").trim();
  if (UNIT.test(t) || BACK_MATTER.test(t)) {
    return { ...ent, level: 0, isChapter: true, afterLesson: false };
  }
  if (LESSON_ONLY.test(t)) {
    return { ...ent, level: 1, isChapter: false, afterLesson: true };
  }
  if (UNIT_TASK.test(t) || CN_ACTIVITY.test(t)) {
    return { ...ent, level: 1, isChapter: false, afterLesson: false };
  }
  if (afterLesson && !ent.isChapter) {
    return { ...ent, level: 2, isChapter: false, afterLesson: true };
  }
  if (
    ent.printPage == null &&
    !ent.isChapter &&
    /^[\u4e00-\u9fff]{2,12}$/.test(t) &&
    !UNIT_TASK.test(t)
  ) {
    return { ...ent, level: 0, isChapter: true, afterLesson: false };
  }
  return { ...ent, afterLesson: false };
}

function looksLikeEntry(line, profile, lexicon = null) {
  const s = bareLine(line);
  if (!s || asTocTitle(s, profile) || titleSet(profile).has(s)) return false;
  if (CN_ENUM_CHAPTER.test(s) || CN_ENUM_SECTION.test(s) || SECTION_JIE.test(s)) return true;
  if (UNIT.test(s) || BACK_MATTER.test(s) || UNIT_TASK.test(s) || LESSON_ONLY.test(s)) return true;
  if (/^[\u4e00-\u9fff]{2,12}$/.test(s)) return true;
  if (/[=\\]/.test(s) || /^(?:\(|当|解)/.test(s)) return false;
  if (isGroupHeading(s) || CHAPTER.test(s) || PART.test(s) || SECTION.test(s) || APPENDIX_ITEM.test(s)) return true;
  const ent = parseTocLine(s, lexicon);
  return ent.printPage != null && Boolean(ent.label);
}

export function looksLikeTocBlob(text, profile = {}, lexicon = null) {
  const lines = (text || "")
    .split(/\r?\n/)
    .map((ln) => ln.trim())
    .filter(Boolean);
  if (!lines.length) return false;
  const hits = lines.filter((ln) => looksLikeEntry(ln, profile, lexicon));
  if (lines.length === 1) return parseTocLine(lines[0], lexicon).printPage != null;
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

function normLabel(label) {
  return String(label || "")
    .trim()
    .replace(/^\*+\s*/, "")
    .replace(/\s+/g, " ");
}

function lookupTocEntry(label, printPage, tocEntries) {
  if (!Array.isArray(tocEntries) || !tocEntries.length) return null;
  const want = normLabel(label);
  if (!want) return null;
  const page = printPage == null || printPage === "" ? null : Number(printPage);
  let labelOnly = null;
  for (const item of tocEntries) {
    if (!item || typeof item !== "object") continue;
    if (normLabel(item.label) !== want) continue;
    const itemPage = item.print_page == null || item.print_page === "" ? null : Number(item.print_page);
    if (page != null && itemPage != null && itemPage === page) return item;
    if (page == null || itemPage == null) labelOnly = labelOnly || item;
  }
  return labelOnly;
}

function rowHtml(entry, printToPdf) {
  const level = Number.isFinite(Number(entry.level)) ? Number(entry.level) : 1;
  const classes = [`kb-page-toc-row`, `kb-page-toc-row--lv${level}`];
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

export function liftPageToc(md, printToPdf = {}, profile = {}, tocEntries = [], lexicon = null) {
  const raw = (md || "").replace(/\r\n/g, "\n");
  const compiled = compileLexicon(lexicon);
  if (raw.includes('class="kb-page-toc"') || !looksLikeTocBlob(raw, profile, compiled)) return raw;
  const lines = raw.split("\n");
  const rows = [];
  let heading = "";
  let afterLesson = false;
  const sources = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const titleText = asTocTitle(trimmed, profile);
    if (titleText) {
      heading = titleText;
      continue;
    }
    if (!looksLikeEntry(trimmed, profile, compiled) && !isGroupHeading(bareLine(trimmed))) continue;
    const parsed = parseTocLine(trimmed, compiled);
    const hit = lookupTocEntry(parsed.label, parsed.printPage, tocEntries);
    let ent;
    let source;
    if (hit) {
      const level = Number(hit.level);
      ent = {
        ...parsed,
        level: Number.isFinite(level) ? level : 1,
        isChapter: Boolean(hit.is_chapter),
        afterLesson: LESSON_ONLY.test((parsed.label || "").trim()),
        source: "toc",
      };
      source = "toc";
      if (Number(hit.level) !== Number(parsed.level)) {
        console.warn(
          JSON.stringify({
            kind: "toc.level",
            label: parsed.label,
            printPage: parsed.printPage,
            from: "toc",
            level: ent.level,
            guessed: parsed.level,
          }),
        );
      }
    } else {
      ent = classifyUnitRow(parsed, afterLesson);
      source = (ent.label || "").trim() ? "guess" : "miss";
      ent = { ...ent, source };
      if (source === "miss") {
        console.warn(
          JSON.stringify({
            kind: "toc.level",
            label: parsed.label,
            printPage: parsed.printPage,
            from: "miss",
          }),
        );
      }
    }
    afterLesson = Boolean(ent.afterLesson);
    sources.push(source);
    rows.push(rowHtml(ent, printToPdf));
  }
  if (rows.length < 2) return raw;
  const navSource = sources.includes("miss") ? "miss" : sources.includes("guess") ? "guess" : "toc";
  const tag = headingTag(profile);
  const title = heading ? `<${tag} class="kb-page-toc-heading">${escapeHtml(heading)}</${tag}>` : "";
  return `${title}<nav class="kb-page-toc" data-toc-source="${navSource}"><ol class="kb-page-toc-list">${rows.join("")}</ol></nav>`;
}
