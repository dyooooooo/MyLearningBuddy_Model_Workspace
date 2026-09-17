export type ImportMeta = {
  label: string;
  textbook_hint: string;
  subject: string;
  grade_band: string;
  category: string;
};

const SUBJECTS: Record<string, string> = {
  math: "math",
  mathematics: "math",
  数学: "math",
  chinese: "chinese",
  yuwen: "chinese",
  语文: "chinese",
  physics: "physics",
  物理: "physics",
  chemistry: "chemistry",
  化学: "chemistry",
  biology: "biology",
  生物: "biology",
  english: "english",
  英语: "english",
};

const CATEGORIES: Record<string, string> = {
  textbook: "textbook",
  教材: "textbook",
  knowledge: "knowledge",
  知识点: "knowledge",
  syllabus: "syllabus",
  考纲: "syllabus",
  material: "material",
  资料: "material",
  homework: "homework",
  作业: "homework",
};

const STAGES: Record<string, string> = {
  ms: "ms",
  junior: "ms",
  初中: "ms",
  hs: "hs",
  senior: "hs",
  高中: "hs",
  ps: "ps",
  小学: "ps",
};

function alias(raw: string, table: Record<string, string>, fallback: string): string {
  const text = (raw || "").trim();
  return table[text.toLowerCase()] || table[text] || fallback;
}

export function parseImportMetaFromTraceDetail(detail: string): ImportMeta | null {
  const text = (detail || "").trim();
  if (!text) return null;
  try {
    return parseImportMeta(JSON.parse(text) as Record<string, unknown>);
  } catch {
    return null;
  }
}

export function importMetaFromJob(job: {
  trace?: { title?: string; detail?: string }[] | null;
}): ImportMeta | null {
  const steps = [...(job.trace || [])].reverse();
  for (const step of steps) {
    if (String(step.title || "") !== "propose_import_meta") continue;
    const meta = parseImportMetaFromTraceDetail(String(step.detail || ""));
    if (meta) return meta;
  }
  return null;
}

export function parseImportMeta(raw: Record<string, unknown> | null | undefined): ImportMeta | null {
  if (!raw || typeof raw !== "object") return null;
  const label = String(raw.label || "").trim();
  if (!label) return null;
  let hint = String(raw.textbook_hint || label).trim();
  if (hint.toLowerCase().endsWith(".pdf")) hint = label;
  return {
    label,
    textbook_hint: hint,
    subject: alias(String(raw.subject || ""), SUBJECTS, "math"),
    grade_band: alias(String(raw.grade_band || ""), STAGES, "hs"),
    category: alias(String(raw.category || ""), CATEGORIES, "textbook"),
  };
}

export const IDENTIFY_TEMPLATE = `刚拖入一本教材 PDF。文件名：{{filename}}。书号：{{book_id}}。
MinerU 已经识别了前三页，文本在下面。不要再 open_pdf_page，不要读 PDF，不要看图。
文本已在对话里：第一件事就调用 propose_import_meta，不要先读 SKILL、不要 glob、不要搜索工作区。
根据文本判断科目、学段、类型、显示名、书名提示。
显示名和书名提示里的册号用阿拉伯数字：写「第1册」不要写「第一册」，方便按标题排序。
科目只能是 math / chinese / physics / chemistry / biology / english，学段只能是 ms（初中）/ hs（高中）/ ps（小学）。
类型只能是 textbook（教材）/ knowledge（知识点）/ syllabus（考纲）/ material（资料）/ homework（作业）。课程标准、命题要求、考试说明归 syllabus。
然后只调用一次 propose_import_meta(book_id, label, textbook_hint, subject, grade_band, category)。
这个工具会写回导入表并立刻入队全书识别，不要等用户点按钮，不要再调 mineru_convert。
不要 refine_page / toc_extract / improve_render / build_toc。
填完后用一两句话说明依据，并说明已经开始全书识别。

----- MinerU 前三页 -----
{{preview}}`;

const PREVIEW_CHAT_CHARS = 60000;

export function clipIdentifyPreview(preview: string, bookId: string): string {
  const text = (preview || "").trim();
  if (!text) {
    return `（MinerU 没有抽出文字。可读 textbooks/${bookId}/identify_preview.md；否则根据文件名判断。）`;
  }
  if (text.length <= PREVIEW_CHAT_CHARS) return text;
  return `${text.slice(0, PREVIEW_CHAT_CHARS)}\n\n…（已截断，全文在 textbooks/${bookId}/identify_preview.md，请 read 该文件后再 propose_import_meta）`;
}

export function renderIdentifyMessage(
  tpl: string,
  bookId: string,
  filename: string,
  preview = "",
): string {
  const body = (tpl || IDENTIFY_TEMPLATE).trim() || IDENTIFY_TEMPLATE;
  return body
    .replaceAll("{{book_id}}", bookId)
    .replaceAll("{{filename}}", filename)
    .replaceAll("{{title}}", "导入教材")
    .replaceAll("{{preview}}", clipIdentifyPreview(preview, bookId));
}
