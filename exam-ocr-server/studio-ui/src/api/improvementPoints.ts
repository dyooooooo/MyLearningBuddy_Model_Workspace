/** Improvement Points API via MinerU corpus proxy `/proxy/improvement-points/*`. */

import { studioFetch, type HttpMethod } from "./http";

const PROXY = "/api/v1/studio/proxy";

async function call<T = unknown>(
  method: string,
  path: string,
  body?: unknown,
  query?: Record<string, string>,
): Promise<T> {
  return studioFetch<T>(`${PROXY}/${path.replace(/^\//, "")}`, {
    method: method as HttpMethod,
    body,
    query,
  });
}

export type ImprovementPoint = {
  id: string;
  fingerprint_hash?: string;
  source_job_id?: string;
  source_session_id?: string;
  locus_key?: string;
  page_no?: number;
  category?: string;
  authority?: string;
  status?: string;
  claim?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  concrete?: Record<string, unknown>;
  procedure?: {
    steps?: Array<{ tool?: string; args_summary?: string; at?: string }>;
    verify?: string;
    avoid?: unknown[];
  };
  pattern_id?: string;
  pattern_status?: string;
  superseded_by?: string;
  merged_into_id?: string;
  created_at?: string;
  updated_at?: string;
  /** ai_auto | user_ask（闭集）；旧码兼容至清理完成 */
  origin?: string;
  origin_label?: string;
  summary?: string;
  kind?: string;
  needs_split?: boolean;
  /** 预抽象整理状态：needs_triage | needs_split | needs_aggregate | ready */
  prep_status?: string;
  prep_status_label?: string;
  /** 需聚合簇 id（同组应拢在一起裁决） */
  aggregate_group_id?: string;
  /** 该簇对立/聚合原因（整理时写入） */
  aggregate_reason?: string;
  /**
   * 改进历程（来历）：拆分/聚合时删旧条前留下的摘要事件。
   * 与 lifecycle「出生故事」分工——这里讲后来怎么整理出来的。
   */
  lineage?: IpLineageEvent[];
  /** 同一轮用户对话派生的改进点共享此键（用于列表折叠） */
  dialogue_key?: string;
  dialogue_size?: number;
  dialogue_lead?: string;
  /** 同定位键下的版本数与被替换掉的旧版 */
  version_count?: number;
  older_count?: number;
  older_ids?: string[];
  apply_layers?: string[];
  apply_layer_label?: string;
  future_plan?: string;
  /** 人话标题（比 claim.summary 更可读） */
  headline?: string;
  what_happened?: string;
  paths?: string[];
  mode?: string;
  mode_label?: string;
  proposer?: string;
  user_input?: string;
  /** 前因链：用户要求 → AI 动作 →（守卫）结果 */
  causal_chain?: {
    user_requests?: string[];
    ai_summary?: string;
    ai_tools?: Array<{ tool?: string; args_summary?: string }>;
    narrative?: string;
  };
  trigger?: {
    job_id?: string;
    session_id?: string;
    page_no?: number;
    mode?: string;
    mode_label?: string;
    proposer?: string;
    authority?: string;
    authority_label?: string;
  };
  procedure_steps_ui?: Array<{
    tool?: string;
    args_summary?: string;
    at?: string;
  }>;
  /** 完整生命周期故事：用户 → AI → 工具 → 结果 → 后续 */
  lifecycle?: {
    stages?: Array<{
      key?: string;
      title?: string;
      body?: string;
      items?: Array<{ text?: string; tool?: string; args_summary?: string }>;
      avoid?: Array<{ tool?: string; args_summary?: string }>;
      paths_label?: string;
      status?: string;
      status_label?: string;
      status_meaning?: string;
      pattern_status?: string;
      pattern_label?: string;
      apply_layers?: string[];
      apply_layer_label?: string;
      next_actions?: string[];
      timeline?: string[];
    }>;
    status?: string;
    status_label?: string;
    next_actions?: string[];
    future_plan?: string;
  };
};

/** 单条改进历程事件（拆分 / 聚合等） */
export type IpLineageSnapshot = {
  id?: string;
  summary?: string;
  page_no?: number;
  prep_status?: string;
  category?: string;
  origin?: string;
  procedure_step_count?: number;
};

export type IpLineageEvent = {
  op?: string;
  at?: string;
  actor?: string;
  summary?: string;
  parent?: IpLineageSnapshot;
  peers?: IpLineageSnapshot[];
  meta?: Record<string, unknown>;
  weak?: boolean;
};

export type IpListOut = {
  items: ImprovementPoint[];
  by_locus: Record<string, ImprovementPoint[]>;
  by_dialogue?: Record<string, ImprovementPoint[]>;
  conflicts: ImprovementPoint[];
  accepted: ImprovementPoint[];
  counts: {
    total: number;
    accepted: number;
    conflict: number;
    superseded?: number;
    rejected?: number;
    pending?: number;
    locus: number;
    dialogue?: number;
    merged?: number;
    by_status?: Record<string, number>;
    by_prep_status?: Record<string, number>;
  };
};

export type PrepStatus =
  | "needs_triage"
  | "needs_split"
  | "needs_aggregate"
  | "ready";

export const PREP_STATUS_LABELS: Record<PrepStatus, string> = {
  needs_triage: "待整理",
  needs_split: "待拆分",
  needs_aggregate: "需聚合",
  ready: "完成体",
};

export function normalizePrepStatus(raw?: string, needsSplit?: boolean): PrepStatus {
  const p = String(raw || "").trim();
  if (p === "needs_conflict_resolve") return "needs_aggregate";
  if (
    p === "needs_triage" ||
    p === "needs_split" ||
    p === "needs_aggregate" ||
    p === "ready"
  ) {
    return p;
  }
  if (needsSplit) return "needs_split";
  return "needs_triage";
}

export type SplitProposal = {
  summary: string;
  category?: string;
  procedure_slice?: Record<string, unknown>;
  procedure?: Record<string, unknown>;
  step_indices?: number[];
  after_indices?: number[];
  after?: unknown[];
};

export type ConflictPair = {
  a_id: string;
  b_id: string;
  page_no?: number;
  reason?: string;
  summary_a?: string;
  summary_b?: string;
  prep_status_a?: string;
  prep_status_b?: string;
};

export type IpConfirmBody = {
  job_id?: string;
  session_id?: string;
  fingerprint_hash?: string;
  page_no?: number;
  category?: string;
  summary?: string;
  authority?: string;
  kind?: string;
  block_ids?: string[];
  slot?: string;
  procedure?: {
    steps?: Array<{ tool?: string; args_summary?: string; at?: string }>;
    verify?: string;
    avoid?: unknown[];
  } | null;
  attempts?: Array<Record<string, unknown>>;
  concrete?: Record<string, unknown> | null;
  pattern_abstract?: string;
  pattern_status?: string;
  edition_family_id?: string;
  write_correction?: boolean;
  origin?: string;
};

export type IpConfirmOut = {
  ok?: boolean;
  ip?: ImprovementPoint;
  correction_id?: string;
  pattern_id?: string;
  superseded_count?: number;
  detail?: string;
  error?: string;
};

/** 落地目标闭集 —— 每个取值对应一个真实生效面（与后端 LANDING_TARGETS 一致）。 */
export const LANDING_TARGETS = {
  "L1.layout_rule": "版式规则（zone 判定）→ layout_rules.yaml",
  "L1.config_pattern": "识别正则与几何阈值 → config.json",
  "L1.theme_css": "排版样式 → assets/theme_css",
  "L0.normalize": "文本归一化 → Python + pytest",
  "L0.renderer": "渲染器行为 → Python",
  "L3.book_patch": "不可规则化 → 保持专书补丁",
} as const;

export type LandingTarget = keyof typeof LANDING_TARGETS;

/** 模式生命周期状态 → 人话（与后端 PATTERN_STATUSES 一致）。 */
export const PATTERN_STATUS_LABELS: Record<string, string> = {
  draft: "草稿（刚建）",
  candidate: "待关注（够格给人看）",
  routed: "已定生效面",
  landed: "已写入生效面",
  no_landing: "专书补丁（不落规则）",
  needs_ip_split: "需先拆改进点",
  approved: "已批准（旧值）",
};

/** 表格/详情用：优先中文，未知取值原样回退。 */
export function patternStatusLabel(status: string | undefined | null): string {
  const key = String(status || "").trim().toLowerCase();
  if (!key) return "—";
  return PATTERN_STATUS_LABELS[key] || key;
}

export type PatternMember = {
  ip_id: string;
  page_no?: number;
  job_id?: string;
  fingerprint_hash?: string;
  summary?: string;
};

export type ImprovementPattern = {
  id: string;
  category?: string;
  abstract_text?: string;
  procedure_template?: Record<string, unknown>;
  target_layer?: string;
  landing_target?: string;
  landing_target_label?: string;
  landing_note?: string;
  edition_family_id?: string;
  /** draft | candidate | routed | landed | no_landing */
  status?: string;
  member_count?: number;
  landed_ref?: string;
  /** 症状名（闭集，与 configs/checks 里 YAML 同名） */
  suspect_type?: string;
  /** 检测器声明文件名，空 = 还查不出来 */
  detector_ref?: string;
  /** rule = 能自动修 · playbook = 只能按手册修 */
  remediation_kind?: string;
  /** 手册出处指针，形如 configs/checks/x.yaml#remediation.playbook */
  playbook_ref?: string;
  /** 按指针解析出来的手册步骤全文（真身在检测器 YAML 里） */
  playbook?: string;
  fingerprints?: string[];
  book_count?: number;
  hits?: number;
  true_positive?: number;
  false_positive?: number;
  created_at?: string;
  updated_at?: string;
  members?: PatternMember[];
  /** 成员页 = 这条模式的黄金集 */
  member_pages?: number[];
};

export type PatternPromotionGate = {
  gate: string;
  passed: boolean;
  detail?: string;
};

export type PatternPromotionCheck = {
  ok?: boolean;
  pattern_id?: string;
  suspect_type?: string;
  current_kind?: string;
  promotable?: boolean;
  gates?: PatternPromotionGate[];
  proposal?: string;
};

export type PatternListOut = {
  patterns: ImprovementPattern[];
  counts: {
    by_landing_target?: Record<string, number>;
    by_status?: Record<string, number>;
  };
  landing_targets?: Record<string, string>;
};

export const patterns = {
  list: (
    q: {
      category?: string;
      status?: string;
      landing_target?: string;
      min_members?: number;
      with_members?: boolean;
      limit?: number;
    } = {},
  ) => {
    const query: Record<string, string> = {};
    if (q.category) query.category = q.category;
    if (q.status) query.status = q.status;
    if (q.landing_target) query.landing_target = q.landing_target;
    if (q.min_members != null) query.min_members = String(q.min_members);
    if (q.with_members) query.with_members = "true";
    if (q.limit != null) query.limit = String(q.limit);
    return call<PatternListOut>("GET", "patterns", undefined, query);
  },
  get: (id: string) =>
    call<ImprovementPattern>("GET", `patterns/${encodeURIComponent(id)}`),
  /** 定路由：这条规律最终写到哪个生效面。 */
  route: (id: string, landing_target: LandingTarget | string, note = "") =>
    call<{ ok?: boolean; pattern?: ImprovementPattern }>(
      "POST",
      `patterns/${encodeURIComponent(id)}/route`,
      { landing_target, note },
    ),
  /** 落地记账：写盘由 Copilot 走批准闸门完成，这里回填 landed_ref。 */
  land: (id: string, landed_ref: string, note = "") =>
    call<{ ok?: boolean; pattern?: ImprovementPattern }>(
      "POST",
      `patterns/${encodeURIComponent(id)}/land`,
      { landed_ref, note },
    ),
  merge: (id: string, merge_ids: string[], note = "") =>
    call<{ ok?: boolean; pattern?: ImprovementPattern; merged_ids?: string[] }>(
      "POST",
      `patterns/${encodeURIComponent(id)}/merge`,
      { merge_ids, note },
    ),
  /** 五道晋级门只读检查：只出提议，不写规则文件。 */
  promotionCheck: (id: string) =>
    call<PatternPromotionCheck>(
      "GET",
      `patterns/${encodeURIComponent(id)}/promotion-check`,
    ),
};

export type IpBookRow = {
  fingerprint_hash: string;
  label: string;
  count: number;
  source_job_id?: string;
  job_fingerprint_mismatch?: boolean;
};

export const improvementPoints = {
  /** 侧栏书列表：按改进点真实指纹分组（含无导入任务的孤儿指纹）。 */
  listBooks: () =>
    call<{ ok?: boolean; total?: number; books?: IpBookRow[] }>(
      "GET",
      "improvement-points/books",
    ),
  list: (q: {
    fingerprint_hash?: string;
    job_id?: string;
    status?: string;
    prep_status?: string;
    page_no?: number;
    limit?: number;
  } = {}) => {
    const query: Record<string, string> = {};
    if (q.fingerprint_hash) query.fingerprint_hash = q.fingerprint_hash;
    if (q.job_id) query.job_id = q.job_id;
    if (q.status) query.status = q.status;
    if (q.prep_status) query.prep_status = q.prep_status;
    if (q.page_no != null) query.page_no = String(q.page_no);
    if (q.limit != null) query.limit = String(q.limit);
    return call<IpListOut>("GET", "improvement-points", undefined, query);
  },
  /** 人确认有效改进 → accepted IP（可派生 L3 corrections）。 */
  confirm: (body: IpConfirmBody) =>
    call<IpConfirmOut>("POST", "improvement-points/confirm", body),
  resolveConflict: (id: string, winner = true) =>
    call("POST", `improvement-points/${encodeURIComponent(id)}/resolve-conflict`, {
      winner,
    }),
  setPattern: (
    id: string,
    body: {
      pattern_status?: string;
      abstract_text?: string;
      target_layer?: string;
      edition_family_id?: string;
    },
  ) => call("POST", `improvement-points/${encodeURIComponent(id)}/pattern`, body),
  converge: (body: { job_id?: string; session_id?: string; fingerprint_hash?: string }) =>
    call("POST", "improvement-points/converge", body),
  /** 彻底删除改进点及库内派生残留（专书页补丁、链接、待审引用等）。 */
  purge: (id: string) =>
    call<{ ok?: boolean; purged?: Record<string, unknown> }>(
      "DELETE",
      `improvement-points/${encodeURIComponent(id)}`,
    ),
  splitPropose: (id: string, children?: SplitProposal[]) =>
    call<{
      ok?: boolean;
      needs_agent?: boolean;
      source_id?: string;
      source_summary?: string;
      proposals?: SplitProposal[];
      errors?: string[];
      unassigned_after_indices?: number[];
      steps?: unknown[];
      after_index?: Array<{ index: number; block_id?: string; preview?: string }>;
    }>("POST", `improvement-points/${encodeURIComponent(id)}/split-propose`, {
      children: children || [],
    }),
  splitApply: (id: string, children: SplitProposal[]) =>
    call<{
      ok?: boolean;
      source_id?: string;
      new_ids?: string[];
      created?: ImprovementPoint[];
    }>("POST", `improvement-points/${encodeURIComponent(id)}/split-apply`, {
      children,
    }),
  conflictPairs: (fingerprint_hash: string, detect = true) => {
    const query: Record<string, string> = {
      fingerprint_hash,
      detect: detect ? "true" : "false",
    };
    return call<{
      ok?: boolean;
      pairs?: ConflictPair[];
      count?: number;
      newly_marked?: ConflictPair[];
    }>("GET", "improvement-points/conflict-pairs", undefined, query);
  },
  conflictResolve: (body: { keep_ids: string[]; delete_ids: string[] }) =>
    call<{
      ok?: boolean;
      deleted_ids?: string[];
      kept?: ImprovementPoint[];
      skipped_ids?: string[];
    }>("POST", "improvement-points/conflict-resolve", body),
  triageApply: (body: {
    fingerprint_hash: string;
    ready_ids?: string[];
    split_ids?: string[];
    aggregate_groups?: Array<{ member_ids: string[]; reason?: string }>;
  }) =>
    call<{ ok?: boolean; applied?: Record<string, string[]> }>(
      "POST",
      "improvement-points/triage-apply",
      body,
    ),
};
