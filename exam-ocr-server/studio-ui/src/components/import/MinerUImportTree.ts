// MinerU 导入 · 左侧「导入」树任务行操作（与 35B 隔离，走 corpus API）

import { copilot, type CopilotSessionSummary } from "../../api/copilot";
import { corpus } from "../../api/corpus";
import type { TreeNode } from "../../api/types";
import { store } from "../../state/store";
import { el } from "../../util/dom";
import { VAULT_ROOT_PREFIX, type VaultTreeHandle } from "../VaultTree";

export const MINERU_JOB_PREFIX = "job:";
/** 「AI 全书导入」已开对话、agent 还没建任务时的占位行。 */
export const MINERU_PENDING_PREFIX = "aiimport:";
export const MINERU_PENDING_NODE_TYPE = "mineru_pending_import";
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

export function parseMinerUPendingSessionId(nodeId: string): string | null {
  return nodeId.startsWith(MINERU_PENDING_PREFIX)
    ? nodeId.slice(MINERU_PENDING_PREFIX.length)
    : null;
}

/** 已收尾的会话不再占位；其余（running / awaiting_* / 未知新状态）都算在跑。 */
const SETTLED_SESSION_STATUS = new Set(["done", "cancelled", "failed"]);

function pendingTreeNode(s: CopilotSessionSummary): TreeNode {
  return {
    id: `${MINERU_PENDING_PREFIX}${s.id}`,
    label: `${s.label || "AI 全书导入"} · 准备中`,
    node_type: MINERU_PENDING_NODE_TYPE,
    status: "pending",
    has_children: false,
    child_count: 0,
    meta_hint: "AI 已接手，任务尚未创建",
  };
}

/**
 * 「AI 全书导入」建了对话、agent 还没调 `corpus.start_import` 的这段时间，
 * 后端一行任务都查不到，刷新页面就像导入丢了。这里用**未绑定任务**的
 * `import_advice` 会话补占位行；等 start_import 落库，会话带上 import_job_id，
 * 占位行自动消失、让位给真正的任务行。
 */
export async function augmentMinerUPendingImports(nodes: TreeNode[]): Promise<TreeNode[]> {
  try {
    const { sessions } = await copilot.listSessions();
    const pending = (sessions || []).filter(
      (s) =>
        s.kind === "import_advice" &&
        !String(s.import_job_id || "").trim() &&
        !SETTLED_SESSION_STATUS.has(s.status),
    );
    if (!pending.length) return nodes;
    return [...pending.map(pendingTreeNode), ...nodes];
  } catch {
    // buddy-copilot 没起来时只是没有占位行，不该让整棵导入树挂掉
    return nodes;
  }
}

async function openPendingAiSession(sessionId: string): Promise<void> {
  const [{ getImportCopilotHost, showImportCopilotTab }, { openCopilotPanel, closeCopilotPanel }] =
    await Promise.all([import("../Inspector"), import("../CopilotPanel")]);
  const host = getImportCopilotHost();
  if (!host) return;
  let payload: Record<string, unknown> = {};
  try {
    payload = (await copilot.getSession(sessionId)).payload || {};
  } catch {
    // 读不到 payload 也要能进对话，恢复靠 resumeSessionId
  }
  showImportCopilotTab();
  closeCopilotPanel(host);
  openCopilotPanel({
    kind: "import_advice",
    host,
    payload,
    resumeSessionId: sessionId,
    title: "AI 全书导入",
  });
}

export function activateMinerUPendingNode(n: TreeNode): boolean {
  const sessionId = parseMinerUPendingSessionId(n.id);
  if (!sessionId) return false;
  store.focusImportWorkspace();
  store.markSelected(n.id);
  importTreeRef?.highlight(n.id);
  void openPendingAiSession(sessionId);
  return true;
}

async function cancelPendingAiSession(sessionId: string, label: string): Promise<void> {
  const plain = label.replace(/ · 准备中$/, "");
  if (!confirm(`取消这次 AI 全书导入「${plain}」？\n（任务还没创建，AI 对话会一并删除）`)) {
    return;
  }
  try {
    await copilot.cancel(sessionId);
  } catch {
    // 线程可能已退出；照样往下删
  }
  try {
    await copilot.deleteSession(sessionId);
  } catch (e) {
    alert(`取消失败：${(e as Error).message}`);
    return;
  }
  const [{ getImportCopilotHost }, { closeCopilotPanel }] = await Promise.all([
    import("../Inspector"),
    import("../CopilotPanel"),
  ]);
  const host = getImportCopilotHost();
  if (host) closeCopilotPanel(host);
  scheduleMinerUTreeRefresh();
}

/** 占位行行尾的「取消」：删会话即可，没有任务要删。 */
function decorateMinerUPendingRow(n: TreeNode, row: HTMLElement): void {
  const sessionId = parseMinerUPendingSessionId(n.id);
  if (!sessionId) return;
  if (row.querySelector(".tree-row-actions--mineru-pending")) return;

  const actions = el(
    "div",
    "tree-row-actions tree-row-actions--always tree-row-actions--mineru-pending",
  );
  const cancelBtn = el("button", "tree-row-action danger", "取消") as HTMLButtonElement;
  cancelBtn.type = "button";
  cancelBtn.title = "取消这次 AI 全书导入（任务尚未创建）";
  cancelBtn.onclick = (e) => {
    e.stopPropagation();
    void cancelPendingAiSession(sessionId, n.label);
  };
  actions.appendChild(cancelBtn);
  row.appendChild(actions);
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

/** 在 MinerU `import_job` 行尾追加删除按钮（占位行走「取消」分支） */
export function decorateMinerUTreeRow(n: TreeNode, row: HTMLElement): void {
  if (n.node_type === MINERU_PENDING_NODE_TYPE) {
    decorateMinerUPendingRow(n, row);
    return;
  }
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
