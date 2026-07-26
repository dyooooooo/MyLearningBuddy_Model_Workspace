import { corpus } from "../api/corpus";
import type { CorpusNode } from "../api/types";
import { store } from "../state/store";
import { refreshVault } from "../components/VaultSidebar";

let deleteInFlight = false;

function parseApiError(err: unknown): string {
  const raw = String(err);
  try {
    const j = JSON.parse(raw) as { detail?: string };
    if (j.detail) return j.detail;
  } catch {
    /* not JSON */
  }
  return raw;
}

export async function deleteNodeWithConfirm(
  node: Pick<CorpusNode, "id" | "label" | "node_type">,
  opts?: { cascade?: boolean; parentId?: string },
): Promise<boolean> {
  if (deleteInFlight) return false;

  const isContainer = node.node_type === "textbook" || node.node_type === "section";
  const cascade = opts?.cascade ?? isContainer;
  const msg = cascade
    ? `确定删除「${node.label}」及其全部子节点？此操作不可恢复。`
    : `确定删除「${node.label}」？此操作不可恢复。`;
  if (!confirm(msg)) return false;

  deleteInFlight = true;
  try {
    await corpus.deleteNode(node.id, cascade);
  } catch (err) {
    alert(`删除失败：${parseApiError(err)}`);
    return false;
  } finally {
    deleteInFlight = false;
  }

  refreshVault();
  if (store.currentNode?.id === node.id) {
    store.currentNode = null;
    store.selectedNodeId = null;
    store.breadcrumb = [];
    store.emit();
  } else if (opts?.parentId) {
    await store.openNode(opts.parentId);
  }
  return true;
}
