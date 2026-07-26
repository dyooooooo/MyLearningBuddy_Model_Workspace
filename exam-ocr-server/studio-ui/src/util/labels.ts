// Display-label helpers (Chinese) for corpus node types, statuses, relations,
// subjects, tags and import/reindex job states. Pure presentation; unknown codes
// fall back to the raw value so nothing ever renders blank.

const NODE_TYPE: Record<string, string> = {
  textbook: "教材",
  section: "章节",
  chapter: "章",
  unit: "单元",
  chunk: "内容块",
  page: "页",
  semantic: "语义块",
  kp: "考纲",
  method: "方法",
  shortcut: "快捷",
  knowledge_point: "考纲",
  question: "题目",
  example: "例题",
  concept: "概念",
  folder: "分组",
  tag: "标签",
  media: "媒体",
};

const STATUS: Record<string, string> = {
  draft: "草稿",
  staging: "暂存",
  review: "待审",
  in_review: "待审",
  approved: "已通过",
  rejected: "已拒绝",
  published: "已发布",
  archived: "归档",
};

/** Edge rel_type → Chinese (includes inverse edges from EDGE_PAIRS). */
const REL_TYPE: Record<string, string> = {
  // structural
  contains_section: "包含章节",
  contains_chunk: "包含内容块",
  in_textbook: "属于教材",
  in_section: "属于章节",
  has_section: "含有章节",
  part_of: "组成",
  belongs_to: "属于",
  // tags
  tagged_with: "打了标签",
  tags_node: "标注节点",
  has_tag: "有标签",
  tag: "标签",
  // kp / syllabus
  primary_kp: "主考纲",
  defines_kp: "定义考纲",
  defined_in: "定义于",
  covers_kp: "覆盖考纲",
  examined_by: "被考查",
  examines: "考查",
  applies_to_kp: "应用于考纲",
  derived_from_kp: "源自考纲",
  prerequisite_of: "前置于",
  related_kp: "相关考纲",
  has_standard_method: "有标准方法",
  has_shortcut: "有快捷",
  // method / shortcut / question
  applies_method: "使用方法",
  method_for: "方法用于",
  uses_shortcut: "使用快捷",
  uses_shortcut_q: "题目使用快捷",
  shortcut_for: "快捷用于",
  enabled_by_method: "由方法启用",
  derived_from_chunk: "源自内容块",
  derives_shortcut: "导出快捷",
  illustrates_method: "例示方法",
  grounded_in_chunk: "基于内容块",
  scoped_in: "范围在",
  covers_kp_tb: "教材覆盖考纲",
  teaches_method: "讲授方法",
  in_textbook_m: "方法属于教材",
  introduces_shortcut: "引入快捷",
  from_textbook: "来自教材",
  section_methods: "章节方法",
  taught_in_section: "方法在章节",
  section_shortcuts: "章节快捷",
  introduced_in_section: "快捷在章节",
  section_questions: "章节题目",
  in_section_q: "题目在章节",
  // content refs
  example_for: "例题属于",
  references_chunk: "引用内容块",
  references: "引用",
  related: "相关",
  related_chunk: "相关内容块",
  // order / family
  prerequisite: "先修",
  required_by: "被需要",
  same_type: "同类型",
  similar_method: "相似方法",
  same_family: "同族",
  follows: "后续",
  preceded_by: "前接",
  successor: "后继",
  predecessor: "前驱",
};

/** K-12 textbook subjects (primary / middle / high). Keys are stable ids. */
const SUBJECT: Record<string, string> = {
  // 主科 / 文理核心（小初高交叉）
  chinese: "语文",
  math: "数学",
  english: "英语",
  // 理科（多为初高中；小学科学另见 science）
  physics: "物理",
  chemistry: "化学",
  biology: "生物",
  science: "科学",
  // 文科 / 社科
  history: "历史",
  geography: "地理",
  politics: "思想政治",
  morality_law: "道德与法治",
  // 外语选修（高中常见）
  japanese: "日语",
  russian: "俄语",
  french: "法语",
  german: "德语",
  spanish: "西班牙语",
  // 技术 / 艺体 / 综合
  information_technology: "信息技术",
  general_technology: "通用技术",
  pe: "体育与健康",
  music: "音乐",
  art: "美术",
  calligraphy: "书法",
  labor: "劳动",
  comprehensive: "综合实践活动",
  mental_health: "心理健康",
};

type SubjectOpt = { value: string; label: string };

const _opt = (value: string, label?: string): SubjectOpt => ({
  value,
  label: label || SUBJECT[value] || value,
});

/** Subjects allowed per grade_band (课标开课范围，非全表堆砌). */
const SUBJECTS_BY_GRADE: Record<string, string[]> = {
  primary: [
    "chinese",
    "math",
    "english",
    "science",
    "morality_law",
    "information_technology",
    "pe",
    "music",
    "art",
    "calligraphy",
    "labor",
    "comprehensive",
    "mental_health",
  ],
  ms: [
    "chinese",
    "math",
    "english",
    "physics",
    "chemistry",
    "biology",
    "history",
    "geography",
    "morality_law",
    "information_technology",
    "pe",
    "music",
    "art",
    "calligraphy",
    "labor",
    "comprehensive",
    "mental_health",
  ],
  hs: [
    "chinese",
    "math",
    "english",
    "physics",
    "chemistry",
    "biology",
    "history",
    "geography",
    "politics",
    "japanese",
    "russian",
    "french",
    "german",
    "spanish",
    "information_technology",
    "general_technology",
    "pe",
    "music",
    "art",
    "labor",
    "mental_health",
  ],
};

function normalizeGradeBand(gradeBand?: string | null): "primary" | "ms" | "hs" {
  const g = String(gradeBand || "").trim().toLowerCase();
  if (g === "primary" || g === "小学") return "primary";
  if (g === "ms" || g === "junior" || g === "初中") return "ms";
  // senior / hs / default → 高中
  return "hs";
}

/** Subject select options filtered by 学段. */
export function subjectOptionsForGrade(gradeBand?: string | null): SubjectOpt[] {
  const key = normalizeGradeBand(gradeBand);
  const ids = SUBJECTS_BY_GRADE[key] || SUBJECTS_BY_GRADE.hs;
  return ids.map((id) => {
    if (id === "science") return _opt(id, "科学");
    if (id === "politics") return _opt(id, "思想政治");
    if (id === "morality_law") return _opt(id, "道德与法治");
    if (id === "general_technology") return _opt(id, "通用技术");
    return _opt(id);
  });
}

/** If subject ∉ 该学段，回落到该学段第一项（通常语文）. */
export function coerceSubjectForGrade(
  subject: string | null | undefined,
  gradeBand?: string | null,
): string {
  const opts = subjectOptionsForGrade(gradeBand);
  const s = String(subject || "").trim();
  if (opts.some((o) => o.value === s)) return s;
  return opts[0]?.value || "chinese";
}

/** @deprecated Use subjectOptionsForGrade — kept for callers expecting a flat list. */
export const SUBJECT_OPTIONS: SubjectOpt[] = subjectOptionsForGrade("hs");

const GRADE: Record<string, string> = {
  primary: "小学",
  ms: "初中",
  hs: "高中",
  senior: "高中",
};

/** Tag key prefixes used in ids/labels like ``tag:grade_band:hs``. */
const TAG_KEY: Record<string, string> = {
  grade_band: "学段",
  subject: "学科",
  edition: "版本",
  locale: "语言",
  content_pack: "内容包",
  doc_type: "文档类型",
  section_kind: "章节类型",
  chunk_kind: "块类型",
};

const JOB_STATUS: Record<string, string> = {
  uploaded: "已上传",
  queued: "排队中",
  pending: "排队中",
  converting: "解析中",
  parsing: "解析中",
  running: "进行中",
  building: "构建中",
  chunking: "切块中",
  embedding: "向量化中",
  done: "已完成",
  succeeded: "已完成",
  failed: "失败",
  error: "失败",
  canceled: "已取消",
  cancelled: "已取消",
};

const REINDEX_STATUS: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  done: "已完成",
  failed: "失败",
  canceled: "已取消",
};

function labelize(map: Record<string, string>, code: string | undefined | null): string {
  const key = String(code ?? "").trim();
  return map[key] || key;
}

function tagValueLabel(key: string, value: string): string {
  if (key === "grade_band" || key === "grade") return gradeLabel(value);
  if (key === "subject") return subjectLabel(value);
  return value;
}

export const QUEUE_LABELS = {
  draft: "草稿块",
  review: "待审节点",
} as const;

export function nodeTypeLabel(nodeType?: string | null): string {
  return labelize(NODE_TYPE, nodeType);
}

export function statusLabel(status?: string | null): string {
  return labelize(STATUS, status);
}

export function relTypeLabel(rel?: string | null): string {
  return labelize(REL_TYPE, rel);
}

export function subjectLabel(subject?: string | null): string {
  return labelize(SUBJECT, subject);
}

export function gradeLabel(gradeBand?: string | null): string {
  return labelize(GRADE, gradeBand);
}

/**
 * Humanize tag node labels / ids: ``tag:grade_band:hs`` → ``学段：高中``.
 */
export function tagDisplayLabel(raw?: string | null): string {
  const s = String(raw ?? "").trim();
  if (!s) return "";
  const body = s.startsWith("tag:") ? s.slice(4) : s;
  const parts = body.split(":").filter(Boolean);
  if (parts.length >= 2) {
    const key = parts[0];
    const value = parts.slice(1).join(":");
    const keyZh = TAG_KEY[key] || key;
    return `${keyZh}：${tagValueLabel(key, value)}`;
  }
  if (parts.length === 1 && TAG_KEY[parts[0]]) {
    return TAG_KEY[parts[0]];
  }
  return s;
}

/** Link list / graph node title: translate tag labels, leave others as-is. */
export function linkNodeLabel(label?: string | null, nodeId?: string | null): string {
  const raw = String(label || nodeId || "").trim();
  if (!raw) return "";
  if (raw.startsWith("tag:") || (nodeId || "").startsWith("tag:")) {
    return tagDisplayLabel(raw.startsWith("tag:") ? raw : nodeId);
  }
  return raw;
}

export function isEnglishSubject(subject?: string | null): boolean {
  const s = String(subject ?? "").toLowerCase();
  return s === "english" || s === "en";
}

export function jobStatusLabel(status?: string | null): string {
  return labelize(JOB_STATUS, status);
}

export function reindexStatusLabel(status?: string | null): string {
  return labelize(REINDEX_STATUS, status);
}
