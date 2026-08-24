/**
 * 检测器判据 ↔ 挂载层词（L2 配置词 / L2b 词插件 / 派生特征）。
 * 中栏目录和右栏详情共用这一套，避免两处各解析一遍 when。
 */
import type {
  ChecksOverview,
  DerivedFeatureInfo,
  SymptomCheckInfo,
  VocabWordInfo,
} from "../api/corpus";

/** 来源层级的人话：改这一层的词，要不要重建镜像。 */
export const LAYER_LABEL: Record<string, string> = {
  L0: "事实字段（镜像内）",
  L1: "算子（镜像内）",
  L2: "配置词（挂载·热更）",
  L2b: "词插件（挂载·受限 Python）",
  derived: "派生特征（挂载·热更）",
};

export const SCOPE_LABEL: Record<string, string> = {
  block: "逐块判定（scope: block）",
  page: "整页判定一次（scope: page）",
};

export const SCOPE_LABEL_SHORT: Record<string, string> = {
  block: "逐块",
  page: "整页",
};

export const HOT_LAYER_ORDER = ["L2", "L2b", "derived"] as const;

const HOT_LAYERS = new Set<string>(HOT_LAYER_ORDER);

export function isHotLayer(layer: string): boolean {
  return HOT_LAYERS.has(layer);
}

/** 挂载层词：L2 配置词、L2b 词插件、派生特征。按层再按名字排。 */
export function hotWords(ov: ChecksOverview): VocabWordInfo[] {
  const rank = Object.fromEntries(HOT_LAYER_ORDER.map((layer, i) => [layer, i]));
  return (ov.vocabulary?.words || [])
    .filter((w) => isHotLayer(w.layer))
    .sort(
      (a, b) =>
        (rank[a.layer] ?? 9) - (rank[b.layer] ?? 9) || a.name.localeCompare(b.name),
    );
}

/** used_by 里存的是检测器文件名，找回那条声明。 */
export function checkBySourceFile(
  ov: ChecksOverview,
  file: string,
): SymptomCheckInfo | null {
  const name = baseName(file);
  if (!name) return null;
  return (ov.checks || []).find((c) => baseName(c.source || "") === name) || null;
}

/** 表达式里的保留字，不是词表里的名字。 */
const EXPR_KEYWORDS = new Set([
  "and", "or", "not", "in", "is", "if", "else", "True", "False", "None",
]);

export function baseName(ref: string): string {
  return String(ref || "").trim().split("/").pop() || "";
}

/** 找这条模式对应的检测器：先对症状名，再退回文件名。 */
export function findCheck(
  ov: ChecksOverview,
  suspect: string,
  detectorRef = "",
): SymptomCheckInfo | null {
  const file = baseName(detectorRef);
  return (
    (ov.checks || []).find((c) => c.suspect_type === suspect) ||
    (file ? (ov.checks || []).find((c) => baseName(c.source || "") === file) : undefined) ||
    null
  );
}

/** when 里用到的派生特征，展开给人看——否则 `is_main_figure` 读不出含义。 */
export function derivedUsedIn(
  expr: string,
  derived: DerivedFeatureInfo[],
): DerivedFeatureInfo[] {
  const text = String(expr || "");
  return (derived || []).filter((d) =>
    new RegExp(`\\b${d.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`).test(text),
  );
}

/** 判据里出现的名字（字符串字面量里的内容不算）。 */
export function namesUsedIn(expr: string): string[] {
  const stripped = String(expr || "")
    .replace(/'([^'\\]|\\.)*'/g, "''")
    .replace(/"([^"\\]|\\.)*"/g, '""');
  const found = stripped.match(/[A-Za-z_][A-Za-z0-9_]*/g) || [];
  return Array.from(new Set(found)).filter((n) => !EXPR_KEYWORDS.has(n));
}

export function vocabWord(name: string, ov: ChecksOverview): VocabWordInfo | null {
  return (ov.vocabulary?.words || []).find((w) => w.name === name) || null;
}

/** 一行词表释义：给中文说明 + 来源层级；配置词与派生特征还给展开式。 */
export function glossaryRow(
  name: string,
  ov: ChecksOverview,
): { kind: string; text: string } | null {
  const docs = ov.vocabulary?.docs || {};
  const word = vocabWord(name, ov);
  if (word) {
    const kind = LAYER_LABEL[word.layer] || word.layer;
    const doc = word.doc || docs[name] || "";
    const expr = String(word.expr || "").trim();
    const parts = [doc, expr ? `= ${expr}` : ""].filter(Boolean);
    return { kind, text: parts.join(" ") || "（未写释义）" };
  }
  const derived = (ov.vocabulary?.derived || []).find((d) => d.name === name);
  if (derived) {
    return { kind: "派生", text: `= ${derived.expr}` };
  }
  if ((ov.vocabulary?.functions || []).includes(name)) {
    return { kind: "函数", text: docs[name] || "（未写释义）" };
  }
  if (docs[name]) {
    const pageOnly = Object.prototype.hasOwnProperty.call(ov.vocabulary?.page || {}, name);
    return { kind: pageOnly ? "页级" : "块级", text: docs[name] };
  }
  return null;
}

export type MountedWordUse = {
  word: VocabWordInfo;
  /** 只有这一条检测器在用 */
  exclusive: boolean;
  /** 最终用到它的检测器条数（含间接） */
  sharedCount: number;
};

/**
 * 这条检测器的 when 里直接用到的挂载层词（L2 / L2b / 派生）。
 * 一对多如实返回：共用词 `exclusive=false`。
 */
export function mountedWordsUsedBy(
  check: SymptomCheckInfo,
  ov: ChecksOverview,
): MountedWordUse[] {
  const names = namesUsedIn(check.when || "");
  const out: MountedWordUse[] = [];
  const seen = new Set<string>();
  for (const name of names) {
    const word = vocabWord(name, ov);
    if (!word || !HOT_LAYERS.has(word.layer) || seen.has(word.name)) continue;
    seen.add(word.name);
    const n = (word.used_by || []).length;
    out.push({ word, exclusive: n <= 1, sharedCount: n });
  }
  return out;
}

export function shareBadge(use: MountedWordUse): string {
  if (use.exclusive) return "专用";
  return `被 ${use.sharedCount} 条共用`;
}
