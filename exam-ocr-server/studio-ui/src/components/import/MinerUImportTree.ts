// MinerU 导入 · 左侧「导入」树任务行操作（与 35B 隔离，走 corpus API）

import { corpus } from "../../api/corpus";
import type { TreeNode } from "../../api/types";
import { store } from "../../state/store";
import { el } from "../../util/dom";
import { VAULT_ROOT_PREFIX, type VaultTreeHandle } from "../VaultTree";

export const MINERU_JOB_PREFIX = "job:";
export const MINERU_JOB_DELETED = "studio:mineru-import-deleted";
export const MINERU_PREVIEW_STATE = "studio:mineru-preview-state";
/** Copilot / 进度轮询触发：增量刷新左栏任务页列表（防抖，不整树闪烁） */
export const MINERU_TREE_REFRESH = "studio:mineru-tree-refresh";
/** Cursor IR / patch 后：中栏指定页原地换 HTML（不拆卡片、不抢滚动） */
export const MINERU_PAGE_SOFT_REFRESH = "studio:mineru-page-soft-refresh";

export interface MinerUPreviewState {
  active: boolean;
  jobId?: string;
  textbookNodeId?: string;
  activePageNo?: number;
}

let importTreeRef: VaultTreeHandle | null = null;
let syncSeq = 0;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let pendingRefreshJobId = "";
let refreshInFlight: Promise<void> | null = null;

export function bindMinerUImportTree(tree: VaultTreeHandle): void {
  importTreeRef = tree;
}

export function parseMinerUJobId(nodeId: string): string | null {
  return nodeId.startsWith(MINERU_JOB_PREFIX)
    ? nodeId.slice(MINERU_JOB_PREFIX.length)
    : null;
}

export function minerUPageChunkId(textbookNodeId: string, pageNo: number): string {
  return `chunk:${textbookNodeId}:p${String(pageNo).padStart(4, "0")}`;
}

async function deleteMinerUJob(jobId: string, label: string): Promise<boolean> {
  if (!confirm(`删除 MinerU 导入任务「${label}」？\n（将同时删除与之绑定的 AI 对话）`)) {
    return false;
  }
  try {
    await corpus.deleteImport(jobId);
    // 网关会级联删 Copilot；本地再清面板/localStorage
    void import("../CopilotPanel").then(({ onImportJobDeleted }) => {
      onImportJobDeleted(jobId);
    });
    document.dispatchEvent(
      new CustomEvent(MINERU_JOB_DELETED, { detail: { jobId } }),
    );
    void importTreeRef?.reload();
    return true;
  } catch (e) {
    alert(`删除失败：${(e as Error).message}`);
    return false;
  }
}

/** 在 MinerU `import_job` 行尾追加删除按钮 */
export function decorateMinerUTreeRow(n: TreeNode, row: HTMLElement): void {
  if (n.node_type !== "import_job") return;
  const jobId = parseMinerUJobId(n.id);
  if (!jobId) return;
  if (row.querySelector(".tree-row-actions--mineru")) return;

  const actions = el("div", "tree-row-actions tree-row-actions--always tree-row-actions--mineru");
  const delBtn = el("button", "tree-row-action danger", "删除") as HTMLButtonElement;
  delBtn.type = "button";
  delBtn.title = "删除此导入任务";
  delBtn.onclick = (e) => {
    e.stopPropagation();
    void deleteMinerUJob(jobId, n.label);
  };
  actions.appendChild(delBtn);
  row.appendChild(actions);
}

async function syncTreeHighlight(state: MinerUPreviewState): Promise<void> {
  if (!importTreeRef || !state.active || !state.jobId) return;
  const seq = ++syncSeq;
  const jobNodeId = `${MINERU_JOB_PREFIX}${state.jobId}`;

  if (!state.activePageNo || state.activePageNo < 1) {
    store.markSelected(jobNodeId);
    importTreeRef.highlight(jobNodeId);
    return;
  }

  const chunkId = state.textbookNodeId
    ? minerUPageChunkId(state.textbookNodeId, state.activePageNo)
    : "";

  store.markSelected(chunkId || jobNodeId);

  if (!chunkId) {
    importTreeRef.highlight(jobNodeId);
    return;
  }

  // Import tree is flat under the job: job → 1,2,3… — avoid expand/reveal every poll tick.
  try {
    const already = !!document.querySelector(
      `.tree-node[data-node-id="${CSS.escape(chunkId)}"]`,
    );
    if (already) {
      importTreeRef.highlight(chunkId);
      return;
    }
    await importTreeRef.expandApiRoot("import");
    if (seq !== syncSeq) return;
    const found = await importTreeRef.revealPath("import", [jobNodeId, chunkId]);
    if (seq !== syncSeq) return;
    if (!found) importTreeRef.highlight(chunkId || jobNodeId);
  } catch {
    importTreeRef.highlight(jobNodeId);
  }
}

/** 增量刷新左栏：导入根（任务行）+ 指定 job 下页列表。节流合并，避免闪烁。 */
export function scheduleMinerUTreeRefresh(jobId?: string): void {
  const id = String(jobId || "").trim();
  if (id) pendingRefreshJobId = id;
  if (refreshTimer != null) window.clearTimeout(refreshTimer);
  refreshTimer = window.setTimeout(() => {
    refreshTimer = null;
    const target = pendingRefreshJobId;
    void runMinerUTreeRefresh(target);
  }, 500);
}

async function runMinerUTreeRefresh(jobId: string): Promise<void> {
  if (!importTreeRef) return;
  if (refreshInFlight) {
    await refreshInFlight;
  }
  const tree = importTreeRef;
  refreshInFlight = (async () => {
    try {
      await tree.expandApiRoot("import");
      await tree.refreshChildren(`${VAULT_ROOT_PREFIX}import`);
      if (jobId) {
        const jobNodeId = `${MINERU_JOB_PREFIX}${jobId}`;
        await tree.refreshChildren(jobNodeId);
      }
    } catch (e) {
      console.warn("MinerU 左栏增量刷新失败", e);
    }
  })();
  try {
    await refreshInFlight;
  } finally {
    refreshInFlight = null;
  }
}

export function initMinerUImportTreeListeners(): void {
  document.addEventListener(MINERU_PREVIEW_STATE, ((e: CustomEvent<MinerUPreviewState>) => {
    const state = e.detail;
    if (state?.active) void syncTreeHighlight(state);
  }) as EventListener);

  document.addEventListener(MINERU_TREE_REFRESH, ((e: CustomEvent<{ jobId?: string }>) => {
    scheduleMinerUTreeRefresh(e.detail?.jobId);
  }) as EventListener);

  // 进度轮询也会推页就绪；与 tree-refresh 共用节流
  document.addEventListener("studio:import-progress", (() => {
    const jobId = store.activeImportJob?.id || pendingRefreshJobId;
    if (jobId) scheduleMinerUTreeRefresh(jobId);
  }) as EventListener);
}

export function emitMinerUPreviewState(state: MinerUPreviewState): void {
  document.dispatchEvent(new CustomEvent(MINERU_PREVIEW_STATE, { detail: state }));
}

export function emitMinerUTreeRefresh(jobId?: string): void {
  document.dispatchEvent(
    new CustomEvent(MINERU_TREE_REFRESH, { detail: { jobId: jobId || "" } }),
  );
}

/** Cursor IR 写盘后：通知中栏对单页做原地 HTML 刷新（无整页重挂）。 */
export function emitMinerUPageSoftRefresh(jobId: string, pageNo: number): void {
  const jid = String(jobId || "").trim();
  const pn = Number(pageNo) || 0;
  if (!jid || pn <= 0) return;
  document.dispatchEvent(
    new CustomEvent(MINERU_PAGE_SOFT_REFRESH, {
      detail: { jobId: jid, pageNo: pn },
    }),
  );
}
