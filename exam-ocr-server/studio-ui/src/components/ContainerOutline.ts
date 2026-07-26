import { corpus } from "../api/corpus";
import type { TreeNode } from "../api/types";
import { store } from "../state/store";
import { el, statusClass } from "../util/dom";
import { openSearchDrawer } from "../views/SearchDrawer";
import { deleteNodeWithConfirm } from "../util/deleteNode";
import { linkNodeLabel, nodeTypeLabel, statusLabel } from "../util/labels";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function renderContainerOutline(
  rootEl: HTMLElement,
  nodeId: string,
  _nodeType: string,
): Promise<void> {
  rootEl.innerHTML = "";
  rootEl.appendChild(el("h3", "outline-title", "目录"));

  const table = el("table", "outline-table");
  const thead = el("thead", "");
  thead.innerHTML =
    "<tr><th></th><th>名称</th><th>类型</th><th>状态</th><th>索引</th><th>操作</th></tr>";
  table.appendChild(thead);
  const tbody = el("tbody", "");
  table.appendChild(tbody);
  rootEl.appendChild(table);

  const loading = el("p", "muted", "加载子节点…");
  rootEl.appendChild(loading);

  let children: TreeNode[] = [];
  try {
    children = await corpus.tree("textbook", nodeId);
  } catch {
    loading.textContent = "无法加载子节点";
    return;
  }
  loading.remove();

  if (!children.length) {
    rootEl.appendChild(el("p", "muted", "暂无子节点"));
    return;
  }

  for (const child of children) {
    tbody.appendChild(outlineRow(child));
  }
}

function outlineRow(n: TreeNode): HTMLElement {
  const tr = el("tr", "outline-row");
  tr.dataset.nodeId = n.id;

  const dot = el("td", "", "");
  dot.appendChild(el("span", `status-dot ${statusClass(n.status)}`, "●"));

  const labelTd = el("td", "outline-label", "");
  const labelBtn = el("button", "link-btn", n.label);
  labelBtn.type = "button";
  labelBtn.onclick = () => store.openNode(n.id);
  labelTd.appendChild(labelBtn);

  tr.appendChild(dot);
  tr.appendChild(labelTd);
  tr.appendChild(el("td", "outline-type", nodeTypeLabel(n.node_type)));
  tr.appendChild(el("td", "outline-status", statusLabel(n.status || "")));
  tr.appendChild(el("td", "outline-index", (n.meta_hint || "").includes("indexed") ? "✓" : "—"));

  const actions = el("td", "outline-actions", "");
  if (n.status === "review") {
    const pub = el("button", "btn-sm", "发布");
    pub.type = "button";
    pub.onclick = async (e) => {
      e.stopPropagation();
      await corpus.publish(n.id);
      await store.openNode(store.currentNode?.id || n.id);
    };
    actions.appendChild(pub);
  }
  const search = el("button", "btn-sm", "试搜");
  search.type = "button";
  search.onclick = async (e) => {
    e.stopPropagation();
    const node = await corpus.getNode(n.id);
    openSearchDrawer(node.summary || node.label, n.id);
  };
  const del = el("button", "btn-sm btn-danger", "删除");
  del.type = "button";
  del.title = "删除节点";
  del.onclick = async (e) => {
    e.stopPropagation();
    const full = await corpus.getNode(n.id);
    await deleteNodeWithConfirm(full, { parentId: store.currentNode?.id });
    if (store.currentNode) {
      const slot = document.querySelector(".container-outline") as HTMLElement | null;
      if (slot) renderContainerOutline(slot, store.currentNode.id, store.currentNode.node_type);
    }
  };
  actions.append(search, del);
  tr.appendChild(actions);

  return tr;
}

export function formatLinkLabel(label: string, nodeId: string): string {
  if (label === nodeId && UUID_RE.test(nodeId)) {
    return `题库引用 ${nodeId.slice(0, 8)}…`;
  }
  return linkNodeLabel(label, nodeId) || label || nodeId;
}
