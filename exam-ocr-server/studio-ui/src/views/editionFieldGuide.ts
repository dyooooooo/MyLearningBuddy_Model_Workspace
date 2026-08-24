/**
 * Edition Bundle field guide — drives form controls + right-rail tutorials.
 * Full mirror of learning-buddy-api/app/services/editions/schema.py
 */

import { subjectOptionsForGrade } from "../util/labels";

export type FieldControl =
  | "text"
  | "number"
  | "select"
  | "toggle"
  | "regex"
  | "readonly"
  | "stringlist"
  | "json"
  | "policy_entries";

export type FieldGuide = {
  path: string;
  label: string;
  control: FieldControl;
  /** Select options when control === "select" */
  options?: { value: string; label: string }[];
  min?: number;
  max?: number;
  step?: number;
  rows?: number;
  /** Short one-liner under the field */
  hint: string;
  /** Longer tutorial for the right rail */
  tutorial: string;
  /** What changes after publish */
  impact: string;
  /** Safe default suggestion */
  tip?: string;
};

export const EDIT_OVERVIEW = {
  title: "如何编辑方案",
  steps: [
    "左侧选中要改的方案。",
    "中栏用表单改全部 Bundle 字段；下方 JSON 会同步，也可直接改高级项。",
    "点「仅保存草稿」试改；确认无误再「保存并发布」。",
    "发布后只影响之后的新导入；已导入的书需对该任务做重新处理才会重算。",
  ],
  dont: [
    "不要手改 meta.id / meta.version / meta.base（只读）；换方案或继承请用左侧「新建」。",
    "不要把坐标填成超过 page_w 的值；页面坐标系默认宽约 1000。",
    "正则（patterns）写错会导致章节/目录识别失败——改前先看右栏说明。",
    "不要把版式规则写进 policy.entries —— MinerU 全书导入完全不读它，写了等于没写；请改 layout_rules.yaml。",
  ],
};

export const FACET_OPTIONS = [
  { value: "layout_geometry", label: "layout_geometry — 版式几何" },
  { value: "zone_hints", label: "zone_hints — 分区提示" },
  { value: "running_chrome", label: "running_chrome — 页眉页脚" },
  { value: "aside_policy", label: "aside_policy — 旁注策略" },
  { value: "toc_style", label: "toc_style — 目录样式" },
  { value: "figure_policy", label: "figure_policy — 插图策略" },
  { value: "math_density", label: "math_density — 公式密度" },
  { value: "pedagogy_roles", label: "pedagogy_roles — 教学角色" },
  { value: "content_correct", label: "content_correct — 内容校正" },
  { value: "layout_rebuild", label: "layout_rebuild — 版式重建" },
  { value: "unit_judge", label: "unit_judge — 单元判定" },
  { value: "vision_layout", label: "vision_layout — 视觉版式" },
];

export const SEVERITY_OPTIONS = [
  { value: "must", label: "must — 必须" },
  { value: "should", label: "should — 应当" },
  { value: "may", label: "may — 可以" },
];

export type PolicyEntryDraft = {
  id: string;
  facet: string;
  severity: string;
  statement: string;
  tags: string[];
};

/** All editable Bundle fields shown as a guided form (schema-complete). */
export const FORM_FIELDS: FieldGuide[] = [
  // ── meta ──────────────────────────────────────────────────────────────
  {
    path: "meta.id",
    label: "方案标识",
    control: "readonly",
    hint: "只读；换方案请左侧「新建」复制",
    tutorial: "Bundle 主键。流水线与导入任务用此 id 绑定方案。创建后不可在表单改名。",
    impact: "不可通过编辑修改；fork 才会产生新 id。",
  },
  {
    path: "meta.version",
    label: "草稿内版本号",
    control: "readonly",
    hint: "只读；由系统维护，发布另有时间戳版本",
    tutorial:
      "Bundle 内的逻辑版本标记，不建议手工改。\n" +
      "真正对外生效的是「保存并发布」生成的 published 版本戳；列表里的已发布版本也来自那里。",
    impact: "只读展示；算法不依赖手改此字段。",
  },
  {
    path: "meta.display_name",
    label: "显示名",
    control: "text",
    hint: "出现在导入下拉与左侧列表",
    tutorial: "给人看的名字，可含中文。不影响流水线逻辑，只影响界面展示。",
    impact: "发布后，导入面板与方案列表显示新名称。",
  },
  {
    path: "meta.base",
    label: "继承基座",
    control: "readonly",
    hint: "只读；新建/复制时自动写入源方案",
    tutorial:
      "父方案 id，由「新建」（fork）时自动设置（常见为 _base 或你复制的源方案）。\n" +
      "不要手改；换继承关系请用左侧「新建」从目标方案复制。",
    impact: "只读展示；fork 时由系统写入。",
  },
  {
    path: "meta.grade_band",
    label: "学段",
    control: "select",
    options: [
      { value: "hs", label: "高中" },
      { value: "ms", label: "初中" },
      { value: "primary", label: "小学" },
    ],
    hint: "先选学段，再选学科（学科列表会跟着变）",
    tutorial:
      "学段决定可选学科范围。切换学段后，学科下拉会刷新；若原学科不属于新学段，会自动改成该学段默认项。",
    impact: "发布后新导入带上学段标签，并约束学科选项。",
  },
  {
    path: "meta.subject",
    label: "学科",
    control: "select",
    // Options filled at runtime from grade_band (see EditionOpsPanel).
    options: subjectOptionsForGrade("hs"),
    hint: "与学段联动：只显示该学段开设的学科",
    tutorial:
      "学科下拉随「学段」过滤，不会出现「高中选小学科学」这类错配。\n" +
      "· 小学：语数英、科学、道法、信息、艺体等\n" +
      "· 初中：语数英、理化生、史地、道法…\n" +
      "· 高中：语数英、理化生、史地、思政、外语选修、信息技术/通用技术…\n" +
      "切换学段时若当前学科不合法，会自动改成该学段默认学科。",
    impact: "发布后新导入任务带上该学科 + 学段标签。",
  },
  {
    path: "meta.description",
    label: "说明",
    control: "text",
    hint: "可选，写给同事看的备注",
    tutorial: "描述这套方案适用哪套教材/版式。纯文档字段，不参与解析算法。",
    impact: "仅展示；不影响导入结果。",
  },

  // ── refine ────────────────────────────────────────────────────────────
  {
    path: "config.refine_mode",
    label: "页级 Refine 模式",
    control: "select",
    options: [
      { value: "skip", label: "skip — 跳过（MinerU 全书常用）" },
      { value: "layout_only", label: "layout_only — 只调版式" },
      { value: "content_correct", label: "content_correct — 内容校正" },
      { value: "full", label: "full — 完整 refine" },
      { value: "rerecognize", label: "rerecognize — 重识别" },
    ],
    hint: "遗留字段：MinerU 全书导入不读它",
    tutorial:
      "历史字段。MinerU 全书导入主链在架构上就不跑大模型 refine，\n" +
      "无论这里填什么都不会触发——不是「按 skip 判断」，是根本没有这条分支。\n" +
      "页级版式问题请改 layout_rules.yaml / overrides/pages，再对该页重算排版。",
    impact: "对当前导入流程无影响。",
    tip: "保持 skip。",
  },
  {
    path: "config.refine_parallel",
    label: "Refine 并行度",
    control: "number",
    min: 1,
    max: 8,
    step: 1,
    hint: "同时 refine 的页数，1–8",
    tutorial: "大于 1 可加快 refine，但会抢占 GPU。机器忙时建议 1–2。",
    impact: "影响导入速度与资源占用。",
  },

  // ── features ──────────────────────────────────────────────────────────
  {
    path: "config.features.columns",
    label: "栏型",
    control: "select",
    options: [
      { value: "single", label: "single — 单栏" },
      { value: "triple_slots", label: "triple_slots — 三栏（沪教常见）" },
    ],
    hint: "版式先验，影响分栏检测",
    tutorial:
      "告诉分栏引擎这本教材大致是几栏。\n" +
      "沪教高中数学多为左右旁注 + 中间正文 → triple_slots。",
    impact: "发布后新导入的分栏/侧栏识别策略改变。",
  },
  {
    path: "config.features.aside",
    label: "旁注位置",
    control: "select",
    options: [
      { value: "none", label: "none — 无旁注" },
      { value: "left", label: "left — 仅左侧" },
      { value: "right", label: "right — 仅右侧" },
      { value: "both", label: "both — 左右都有（沪教）" },
    ],
    hint: "侧栏/旁注出现在哪边",
    tutorial: "与 geometry 的 aside_left / aside_right 配合。both = 左右都可能有旁注块。",
    impact: "影响侧栏归属与正文提取。",
  },
  {
    path: "config.features.toc_style",
    label: "目录样式",
    control: "select",
    options: [
      { value: "text_list", label: "text_list — 文本列表" },
      { value: "table", label: "table — 表格式" },
      { value: "mixed", label: "mixed — 混合" },
    ],
    hint: "目录页的版式先验",
    tutorial: "影响目录识别与渲染策略。沪教多为 text_list；引擎把目录当 table 时可对照调整。",
    impact: "影响目录页结构化与 TOC 渲染。",
  },
  {
    path: "config.features.figure_policy",
    label: "插图策略",
    control: "select",
    options: [
      { value: "strict_label", label: "strict_label — 严格图注" },
      { value: "loose", label: "loose — 宽松配对" },
      { value: "caption_first", label: "caption_first — 图注优先" },
    ],
    hint: "图与说明如何配对",
    tutorial: "strict_label 要求明确「图 x-x」类标签；宽松模式适合图注不规范的材料。",
    impact: "影响图注配对与 figure 相关 policy 倾向。",
  },
  {
    path: "config.features.math_density",
    label: "公式密度",
    control: "select",
    options: [
      { value: "low", label: "low" },
      { value: "medium", label: "medium" },
      { value: "high", label: "high — 数学教材常用" },
    ],
    hint: "影响公式相关策略强度",
    tutorial: "高密度会加强公式相关 policy / 处理倾向。语文类可降为 low。",
    impact: "影响公式处理与 prompt 侧重点。",
  },
  {
    path: "config.features.running_header_footer",
    label: "页眉页脚",
    control: "toggle",
    hint: "是否按「有页眉页脚」处理",
    tutorial: "开启后更容易把页眉页脚当 chrome 剥离，避免进正文。",
    impact: "影响页眉页脚是否混入正文。",
  },

  // ── geometry ──────────────────────────────────────────────────────────
  {
    path: "config.geometry.page_w",
    label: "页面逻辑宽度",
    control: "number",
    min: 100,
    max: 5000,
    step: 1,
    hint: "坐标系宽度，默认 1000",
    tutorial:
      "所有 geometry 坐标都以这个宽度为参考（归一化页面宽）。\n" +
      "一般保持 1000，不要随便改，否则左右侧栏阈值会整体错位。",
    impact: "牵一发而动全身，改完建议用样页 reprocess 验证。",
    tip: "默认 1000。",
  },
  {
    path: "config.geometry.wide_min",
    label: "宽块最小宽度",
    control: "number",
    min: 0,
    max: 1000,
    step: 1,
    hint: "宽于该值更容易当主栏/通栏",
    tutorial: "用于区分「窄旁注」与「宽正文块」。旁注过宽被当正文时，可略增大。",
    impact: "影响主栏/旁注宽度判定。",
  },
  {
    path: "config.geometry.aside_max_width",
    label: "旁注最大宽度",
    control: "number",
    min: 0,
    max: 1000,
    step: 1,
    hint: "旁注带最大允许宽度（默认约 320）",
    tutorial: "分栏检测：超过此宽度的条带不太像旁注。旁注被误判成主栏时可略增大。",
    impact: "影响窄带/宽带分类。",
  },
  {
    path: "config.geometry.min_gutter",
    label: "最小栏间距",
    control: "number",
    min: 0,
    max: 200,
    step: 1,
    hint: "栏与栏之间的最小空隙",
    tutorial: "分栏检测用的 gutter 阈值。栏缝很窄的书可略减小。",
    impact: "影响能否拆成多栏。",
  },
  {
    path: "config.geometry.full_width_ratio",
    label: "通栏宽度比",
    control: "number",
    min: 0,
    max: 1,
    step: 0.01,
    hint: "相对 page_w 的通栏判定比例（默认 0.72）",
    tutorial: "块宽度 ≥ page_w × 该比例 → 更易判为通栏/full_bleed。",
    impact: "影响通栏与分栏块的划分。",
  },
  {
    path: "config.geometry.min_y_overlap",
    label: "纵向重叠最小高度",
    control: "number",
    min: 0,
    max: 200,
    step: 1,
    hint: "旁注与主栏「伴生」所需 y 重叠（默认约 18）",
    tutorial: "侧栏块需与主栏在 y 方向有足够重叠才算伴生旁注，避免孤立窄条误判。",
    impact: "影响旁注伴生判定。",
  },
  {
    path: "config.geometry.min_companion_width",
    label: "伴生主栏最小宽度",
    control: "number",
    min: 0,
    max: 1000,
    step: 1,
    hint: "旁注旁侧主栏至少多宽（默认约 200）",
    tutorial: "防止没有足够宽正文伴随时把窄条当成旁注。",
    impact: "影响旁注/正文切割稳健性。",
  },
  {
    path: "config.geometry.aside_left_x1_max",
    label: "左旁注右边界",
    control: "number",
    min: 0,
    max: 1000,
    step: 1,
    hint: "x1 ≤ 此值视为左旁注（默认约 280）",
    tutorial:
      "在 page_w=1000 下，块的右边缘 x1 小于等于该值 → 判为左旁注。\n" +
      "旁注吃进正文：把数值调小；正文被误判成旁注：调大。\n" +
      "表单会同步写入 config.zones.aside_left_x1_max。",
    impact: "直接影响左栏/正文切割。",
    tip: "沪教常见 260–300。",
  },
  {
    path: "config.geometry.aside_right_x0_min",
    label: "右旁注左边界",
    control: "number",
    min: 0,
    max: 1000,
    step: 1,
    hint: "x0 ≥ 此值视为右旁注（默认约 680）",
    tutorial:
      "块的左边缘 x0 大于等于该值 → 判为右旁注。\n" +
      "表单会同步写入 config.zones.aside_right_x0_min。",
    impact: "直接影响右栏/正文切割。",
    tip: "沪教常见 650–720。",
  },
  {
    path: "config.geometry.figure_y_tolerance",
    label: "图注纵向容差",
    control: "number",
    min: 0,
    max: 200,
    step: 1,
    hint: "图与说明在 y 方向的对齐容差",
    tutorial: "图注离图较远时增大；乱挂时减小。会同步到 config.figure_group.y_tolerance。",
    impact: "影响图与说明的配对。",
  },

  // ── patterns ──────────────────────────────────────────────────────────
  {
    path: "config.patterns.chapter",
    label: "章标题正则",
    control: "regex",
    hint: "匹配「第×章 …」",
    tutorial: "正则字符串（不是 /.../ 字面量）。改错会导致目录树缺章。",
    impact: "影响章节切分与目录结构。",
  },
  {
    path: "config.patterns.lesson",
    label: "节标题正则",
    control: "regex",
    hint: "匹配「1.1 xxx」一类小节",
    tutorial: "用于识别课/节标题。与教材编号风格不一致时需要改。",
    impact: "影响节级目录与滚动定位。",
  },
  {
    path: "config.patterns.fig_label",
    label: "图标签正则",
    control: "regex",
    hint: "匹配「图 1-1-1」行首标签",
    tutorial: "识别独立图号行。与 figure_caption 可分工：label 偏行首，caption 偏配对。",
    impact: "影响插图标签识别。",
  },
  {
    path: "config.patterns.fig_ref",
    label: "图引用正则",
    control: "regex",
    hint: "匹配正文「如图…」「图…所示」",
    tutorial: "正文里对图的引用，用于交叉引用/锚点，不是图注本身。",
    impact: "影响图引用检测。",
  },
  {
    path: "config.patterns.solution_lead",
    label: "「解」引导正则",
    control: "regex",
    hint: "匹配解答起笔「解」",
    tutorial: "用于把「解」与后续编号答案标记为 solution_role，控制缩进与样式。",
    impact: "影响解答块排版与角色标记。",
  },
  {
    path: "config.patterns.toc_trailing_page",
    label: "目录行末页码正则",
    control: "regex",
    hint: "拆目录「标题 … 页码」",
    tutorial: "捕获组：标题 + 书页码。书页码 ≠ PDF page_no。",
    impact: "影响目录条目解析与跳转映射。",
  },
  {
    path: "config.patterns.chapter_toc",
    label: "目录章行正则",
    control: "regex",
    hint: "目录里「第 N 章」行",
    tutorial: "目录专用章行（可与正文 chapter 略有不同，例如允许空格）。",
    impact: "影响目录层级（章级）。",
  },
  {
    path: "config.patterns.toc_section",
    label: "目录节号正则",
    control: "regex",
    hint: "匹配 1.1 / 1.1.1 节号",
    tutorial: "目录节/小节编号。注意转义：JSON 里反斜杠需写成 \\\\。",
    impact: "影响目录节级缩进。",
  },
  {
    path: "config.patterns.toc_summary",
    label: "目录小结类标题",
    control: "regex",
    hint: "内容提要/复习题/习题/小结等",
    tutorial: "把非编号的目录功能条目标出来，避免误当章节号。",
    impact: "影响目录特殊行分类。",
  },
  {
    path: "config.patterns.toc_titles",
    label: "目录页标题词",
    control: "stringlist",
    rows: 3,
    hint: "每行一个；如「目录」「目 录」",
    tutorial: "命中这些标题的页更容易被当成目录页，触发 TOC 提升逻辑。",
    impact: "影响目录页检测。",
  },
  {
    path: "config.patterns.figure_caption",
    label: "图注正则（可选）",
    control: "regex",
    hint: "空=不用；有则写入 figure_group.caption_pattern",
    tutorial: "可选覆盖。填写后会同步到 config.figure_group.caption_pattern。",
    impact: "影响图注配对正则。",
  },
  {
    path: "config.patterns.pedagogy_patterns",
    label: "教学角色正则表",
    control: "json",
    rows: 6,
    hint: 'JSON 对象，如 {"think":"^思考","note":"^注"}',
    tutorial:
      "role名 → 正则。用于把块标成 think/note/prompt 等 pedagogy_role。\n" +
      "必须是合法 JSON 对象。",
    impact: "影响旁注/提示类角色识别与渲染样式。",
  },

  // ── zones / figure_group (legacy) ─────────────────────────────────────
  {
    path: "config.zones",
    label: "zones（遗留扁平）",
    control: "json",
    rows: 5,
    hint: "JSON；常含 aside_left_x1_max / aside_right_x0_min",
    tutorial:
      "兼容旧 layout_enrich 消费者。改 geometry 旁注边界时表单会自动同步关键键。\n" +
      "一般保持与 geometry 一致即可。",
    impact: "部分 enrich 路径仍读 zones。",
  },
  {
    path: "config.figure_group",
    label: "figure_group（遗留）",
    control: "json",
    rows: 5,
    hint: "JSON；常含 y_tolerance / caption_pattern",
    tutorial: "图组成组配置。geometry.figure_y_tolerance 与 patterns.figure_caption 会同步写入。",
    impact: "影响图组配对。",
  },

  // ── policy ────────────────────────────────────────────────────────────
  {
    path: "policy.version",
    label: "Policy 文档版本",
    control: "text",
    hint: "policy 文档自身版本，默认 1",
    tutorial: "与 Bundle meta.version 独立。改 entries 时可顺手 bump。",
    impact: "文档标记；过滤逻辑主要看 entries。",
  },
  {
    path: "policy.entries",
    label: "软规则条目",
    control: "policy_entries",
    hint: "⚠️ 账本用途：MinerU 全书导入不读它；卡片编辑 id / facet / severity / 陈述 / tags",
    tutorial:
      "软政策：按 facet 注入对应提示词槽位（仅大模型步骤）。\n" +
      "⚠️ MinerU 全书导入主链不跑大模型，因此本数组对该线【完全无效】；\n" +
      "它目前只对 35B 线的 vision_layout 槽位有效。\n" +
      "AI 会话收尾（finalize）也写进这里 —— 那是账本与溯源，不会改变下次导入结果。\n" +
      "· id：唯一标识（建议 方案前缀.主题）\n" +
      "· facet：提示词过滤主题\n" +
      "· severity：must / should / may\n" +
      "· statement：自然语言规则\n" +
      "· tags：逗号分隔可选标签",
    impact: "只影响 35B 线提示词内容；不改分栏引擎，不影响 MinerU 导入。",
    tip: "要让规则真的生效，请改 layout_rules.yaml（即时）或 geometry/patterns/features（需发布）。",
  },

  // ── assets ────────────────────────────────────────────────────────────
  {
    path: "assets.theme_css",
    label: "主题 CSS 文件名",
    control: "text",
    hint: "相对 assets/ 的文件名，默认 theme.css",
    tutorial: "方案资源目录下的样式表文件名。改名需确保磁盘上确有该文件。",
    impact: "影响出稿 HTML 皮肤引用。",
  },
  {
    path: "assets.glyph_map",
    label: "字形替换表",
    control: "json",
    rows: 5,
    hint: 'JSON 对象，如 {"ﬁ":"fi"}',
    tutorial: "OCR/字体连字等到规范字符的映射。空对象 {} 表示不替换。",
    impact: "影响文本规范化。",
  },
];

const BY_PATH = new Map(FORM_FIELDS.map((f) => [f.path, f]));

export function guideForPath(path: string): FieldGuide | undefined {
  return BY_PATH.get(path);
}

/** Best-effort match when cursor is in JSON (path prefix). */
export function guideForJsonPathHint(text: string, cursor: number): FieldGuide | undefined {
  const before = text.slice(0, cursor);
  const keyMatch = before.match(/"([a-zA-Z0-9_]+)"\s*:\s*[^,\{\[]*$/);
  if (!keyMatch) return undefined;
  const key = keyMatch[1];
  const hits = FORM_FIELDS.filter((f) => f.path.endsWith(`.${key}`) || f.path === key);
  return hits[hits.length - 1];
}

export function getByPath(obj: unknown, path: string): unknown {
  const parts = path.split(".");
  let cur: unknown = obj;
  for (const p of parts) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}

export function setByPath(obj: Record<string, unknown>, path: string, value: unknown): void {
  const parts = path.split(".");
  let cur: Record<string, unknown> = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i];
    const next = cur[p];
    if (next == null || typeof next !== "object" || Array.isArray(next)) {
      cur[p] = {};
    }
    cur = cur[p] as Record<string, unknown>;
  }
  cur[parts[parts.length - 1]] = value;
}
