// 35B 导入 · 左侧「导入」树节点（挂到 VaultTree 下，不碰 corpus API）

import { import35b, type Import35bFeedbackItem } from "../../api/import35b";
import type { TreeNode } from "../../api/types";
import { store } from "../../state/store";
import { el } from "../../util/dom";
import { jobStatusLabel } from "../../util/labels";
import type { VaultTreeHandle } from "../VaultTree";

export const B35_JOB_PREFIX = "b35job:";
export const B35_PAGE_PREFIX = "b35page:";

export interface B35PreviewPage {
  pageNo: number;
  feedbackCount: number;
}

export interface B35PreviewState {
  active: boolean;
  jobId?: string;
  label?: string;
  pages?: B35PreviewPage[];
  activePageNo?: number;
}

export const B35_PREVIEW_STATE = "studio:b35-preview-state";
export const B35_FEEDBACK_STATE = "studio:b35-feedback-state";
export const B35_NAVIGATE = "studio:b35-navigate";
export const B35_GOTO_PAGE = "studio:b35-goto-page";
export const B35_JOB_DELETED = "studio:b35-job-deleted";
export const B35_OPEN_JOB = "studio:b35-open-job";
export const B35_IMPORT_LINE = "studio:import-line-b35";

let importTreeRef: VaultTreeHandle | null = null;
let pageFeedbackMap = new Map<number, number>();
let jobFeedbackCache = new Map<string, Import35bFeedbackItem[]>();
let refreshTimer: ReturnType<typeof setTimeout> | null = null;

export function bindB35ImportTree(tree: VaultTreeHandle): void {
  importTreeRef = tree;
}

export function emitB35PreviewState(state: B35PreviewState): void {
  document.dispatchEvent(new CustomEvent(B35_PREVIEW_STATE, { detail: state }));
}

export function parseB35JobId(nodeId: string): string | null {
  return nodeId.startsWith(B35_JOB_PREFIX) ? nodeId.slice(B35_JOB_PREFIX.length) : null;
}

export function parseB35PageId(nodeId: string): { jobId: string; pageNo: number } | null {
  if (!nodeId.startsWith(B35_PAGE_PREFIX)) return null;
  const rest = nodeId.slice(B35_PAGE_PREFIX.length);
  const sep = rest.lastIndexOf(":");
  if (sep <= 0) return null;
  const jobId = rest.slice(0, sep);
  const pageNo = Number(rest.slice(sep + 1));
  if (!jobId || !Number.isFinite(pageNo) || pageNo < 1) return null;
  return { jobId, pageNo };
}

function jobTreeNode(job: {
  id: string;
  label: string;
  status: string;
  page_count?: number;
  meta?: Record<string, unknown>;
}): TreeNode {
  const progress = String(job.meta?.progress || "");
  const hint = progress ? ` · ${progress}` : "";
  return {
    id: `${B35_JOB_PREFIX}${job.id}`,
    label: `35B · ${job.label || job.id}`,
    node_type: "b35_import_job",
    status: job.status,
    has_children: true,
    child_count: job.page_count || 0,
    meta_hint: `${jobStatusLabel(job.status)}${hint}`,
  };
}

function pageTreeNode(jobId: string, pageNo: number, feedbackCount = 0): TreeNode {
  const badge = feedbackCount > 0 ? ` · ${feedbackCount} 条反馈` : "";
  return {
    id: `${B35_PAGE_PREFIX}${jobId}:${pageNo}`,
    label: `第 ${pageNo} 页${badge}`,
    node_type: "b35_import_page",
    status: feedbackCount > 0 ? "feedback" : "",
    has_children: false,
    child_count: 0,
  };
}

function setJobFeedback(jobId: string, items: Import35bFeedbackItem[]): void {
  jobFeedbackCache.set(jobId, items);
  pageFeedbackMap = new Map<number, number>();
  for (const item of items) {
    if (item.status !== "open") continue;
    pageFeedbackMap.set(item.page_no, (pageFeedbackMap.get(item.page_no) || 0) + 1);
  }
}

/** 挂在「导入」根节点下：35B 任务列表（排在 MinerU corpus 任务之前） */
export async function augmentImportRootChildren(nodes: TreeNode[]): Promise<TreeNode[]> {
  try {
    const jobs = await import35b.jobs();
    const b35Nodes = jobs.map((j) => jobTreeNode(j));
    return [...b35Nodes, ...nodes];
  } catch {
    return nodes;
  }
}

function feedbackCountByPage(jobId: string): Map<number, number> {
  const counts = new Map<number, number>();
  for (const item of jobFeedbackCache.get(jobId) || []) {
    if (item.status !== "open") continue;
    counts.set(item.page_no, (counts.get(item.page_no) || 0) + 1);
  }
  return counts;
}

/** 展开 35B 任务：页列表（反馈数标在页节点上） */
export async function fetchB35JobChildren(parentId: string): Promise<TreeNode[] | null> {
  const jobId = parseB35JobId(parentId);
  if (jobId) {
    try {
      if (!jobFeedbackCache.has(jobId)) {
        try {
          const data = await import35b.listFeedback(jobId);
          setJobFeedback(jobId, data.items || []);
        } catch {
          setJobFeedback(jobId, []);
        }
      }
      const info = await import35b.pages(jobId);
      const counts = feedbackCountByPage(jobId);
      return [...info.pages]
        .sort((a, b) => a.page_no - b.page_no)
        .map((p) => pageTreeNode(jobId, p.page_no, counts.get(p.page_no) || 0));
    } catch {
      return [];
    }
  }

  return null;
}

async function deleteB35Job(jobId: string, label: string): Promise<boolean> {
  const plain = label.replace(/^35B · /, "");
  if (!confirm(`删除 35B 导入任务「${plain}」？`)) return false;
  try {
    await import35b.deleteJob(jobId);
    jobFeedbackCache.delete(jobId);
    document.dispatchEvent(new CustomEvent(B35_JOB_DELETED, { detail: { jobId } }));
    void importTreeRef?.reload();
    return true;
  } catch (e) {
    alert(`删除失败：${(e as Error).message}`);
    return false;
  }
}

/** 供导入首页任务列表等复用 */
export async function deleteB35ImportJob(jobId: string, label: string): Promise<boolean> {
  return deleteB35Job(jobId, label);
}

export function decorateB35TreeRow(n: TreeNode, row: HTMLElement): void {
  const jobId = parseB35JobId(n.id);
  if (!jobId) return;
  if (row.querySelector(".tree-row-actions--b35")) return;
  const actions = el("div", "tree-row-actions tree-row-actions--always tree-row-actions--b35");
  const delBtn = el("button", "tree-row-action danger", "删除") as HTMLButtonElement;
  delBtn.type = "button";
  delBtn.title = "删除此导入任务";
  delBtn.onclick = (e) => {
    e.stopPropagation();
    void deleteB35Job(jobId, n.label);
  };
  actions.appendChild(delBtn);
  row.appendChild(actions);
}

export function activateB35TreeNode(n: TreeNode): boolean {
  const page = parseB35PageId(n.id);
  if (page) {
    if (store.activeRibbon !== "import") store.focusImportWorkspace();
    if (store.importCenterView !== "preview") store.showImportPreview();
    document.dispatchEvent(new CustomEvent(B35_IMPORT_LINE));
    document.dispatchEvent(
      new CustomEvent(B35_GOTO_PAGE, {
        detail: { jobId: page.jobId, pageNo: page.pageNo },
      }),
    );
    importTreeRef?.highlight(n.id);
    return true;
  }

  const jobId = parseB35JobId(n.id);
  if (jobId) {
    store.focusImportWorkspace();
    store.showImportPreview();
    document.dispatchEvent(new CustomEvent(B35_IMPORT_LINE));
    document.dispatchEvent(
      new CustomEvent(B35_NAVIGATE, {
        detail: { jobId, label: n.label.replace(/^35B · /, "") },
      }),
    );
    return true;
  }

  return false;
}

function scheduleTreeRefresh(): void {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    void importTreeRef?.reload();
  }, 280);
}

let syncSeq = 0;

function treeHasNode(nodeId: string): boolean {
  return !!document.querySelector(`.tree-node[data-node-id="${CSS.escape(nodeId)}"]`);
}

async function syncTreeHighlight(state: B35PreviewState): Promise<void> {
  if (!importTreeRef || !state.active || !state.jobId) return;
  const seq = ++syncSeq;
  const jobNodeId = `${B35_JOB_PREFIX}${state.jobId}`;

  if (!state.activePageNo) {
    store.markSelected(jobNodeId);
    importTreeRef.highlight(jobNodeId);
    return;
  }

  const pageId = `${B35_PAGE_PREFIX}${state.jobId}:${state.activePageNo}`;
  store.markSelected(pageId);

  if (treeHasNode(pageId)) {
    importTreeRef.highlight(pageId);
    return;
  }

  try {
    await importTreeRef.expandApiRoot("import");
    if (seq !== syncSeq) return;
    const found = await importTreeRef.revealPath("import", [jobNodeId, pageId]);
    if (seq !== syncSeq) return;
    if (!found) importTreeRef.highlight(jobNodeId);
  } catch {
    importTreeRef.highlight(jobNodeId);
  }
}

export function initB35ImportTreeListeners(): void {
  document.addEventListener(B35_PREVIEW_STATE, ((e: CustomEvent<B35PreviewState>) => {
    const state = e.detail;
    pageFeedbackMap = new Map(
      (state?.pages || []).map((p) => [p.pageNo, p.feedbackCount]),
    );
    if (state?.active) void syncTreeHighlight(state);
  }) as EventListener);

  document.addEventListener(B35_FEEDBACK_STATE, ((e: CustomEvent<{
    jobId: string;
    items: Import35bFeedbackItem[];
  }>) => {
    const { jobId, items } = e.detail || {};
    if (!jobId) return;
    setJobFeedback(jobId, items || []);
    scheduleTreeRefresh();
  }) as EventListener);
}
