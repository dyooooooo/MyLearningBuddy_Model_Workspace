import { corpus } from "../api/corpus";
import type { TreeNode } from "../api/types";
import { el, statusClass } from "../util/dom";
import { createTreeChevron, setTreeChevronExpanded } from "../util/treeChevron";

export const VAULT_ROOT_PREFIX = "vault-root:";

export const VAULT_ROOTS: { key: string; label: string; icon: string }[] = [
  { key: "textbook", label: "教材", icon: "📚" },
  { key: "kp", label: "考纲", icon: "📋" },
  { key: "method", label: "方法", icon: "🧮" },
  { key: "shortcut", label: "口诀", icon: "💡" },
  { key: "question", label: "题库", icon: "❓" },
  { key: "import", label: "导入", icon: "📥" },
  { key: "inbox", label: "收件", icon: "✉️" },
];

const EXPANDED_STORAGE_KEY = "studio-vault-expanded";

export interface VaultTreeOptions {
  onActivate?: (node: TreeNode) => void;
  roots?: string[];
  /** 在 corpus 子节点之后追加（如 35B 任务挂在「导入」根下） */
  augmentChildren?: (
    parentId: string,
    apiRoot: string,
    nodes: TreeNode[],
  ) => Promise<TreeNode[]>;
  /** 完全由外部提供子节点（如 b35job:* 展开为页列表） */
  fetchExtraChildren?: (parentId: string, apiRoot: string) => Promise<TreeNode[] | null>;
  /** 在行尾追加操作按钮（如 35B 任务删除） */
  decorateRow?: (node: TreeNode, row: HTMLElement) => void;
  /** 点击左侧树「导入」等 vault 根节点 */
  onVaultRootClick?: (apiRoot: string) => void;
}

export interface VaultTreeHandle {
  reload: () => Promise<void>;
  /** 增量刷新某节点的子列表（DOM 复用，避免整树闪烁）。 */
  refreshChildren: (parentId: string) => Promise<void>;
  revealPath: (apiRoot: string, path: string[]) => Promise<boolean>;
  highlight: (nodeId: string) => void;
  expandApiRoot: (apiRoot: string) => Promise<void>;
}

function vaultRootId(key: string): string {
  return `${VAULT_ROOT_PREFIX}${key}`;
}

function isVaultRoot(id: string): boolean {
  return id.startsWith(VAULT_ROOT_PREFIX);
}

function apiRootFromVaultRoot(id: string): string {
  return id.slice(VAULT_ROOT_PREFIX.length);
}

function virtualRootNodes(allowedRoots?: string[]): TreeNode[] {
  const roots = allowedRoots?.length
    ? VAULT_ROOTS.filter((r) => allowedRoots.includes(r.key))
    : VAULT_ROOTS;
  return roots.map((r) => ({
    id: vaultRootId(r.key),
    label: `${r.icon} ${r.label}`,
    node_type: "vault_root",
    status: "",
    child_count: 0,
    has_children: true,
    meta_hint: "",
  }));
}

function loadExpandedFromStorage(): Set<string> {
  try {
    const raw = localStorage.getItem(EXPANDED_STORAGE_KEY);
    if (!raw) return new Set([vaultRootId("textbook")]);
    const parsed = JSON.parse(raw) as string[];
    if (!Array.isArray(parsed) || !parsed.length) return new Set([vaultRootId("textbook")]);
    return new Set(parsed);
  } catch {
    return new Set([vaultRootId("textbook")]);
  }
}

function saveExpandedToStorage(ids: Set<string>): void {
  localStorage.setItem(EXPANDED_STORAGE_KEY, JSON.stringify([...ids]));
}

export function mountVaultTree(
  container: HTMLElement,
  options: VaultTreeOptions,
): VaultTreeHandle {
  const allowedRoots = options.roots;
  const expandedIds = loadExpandedFromStorage();

  function isExpandable(n: TreeNode): boolean {
    if (n.node_type === "vault_root") return true;
    if (n.node_type === "b35_review_folder") return true;
    return n.has_children || n.id.startsWith("folder:");
  }

  function isActivatable(n: TreeNode): boolean {
    if (n.node_type === "vault_root") return false;
    if (n.node_type === "b35_review_folder") return false;
    if (n.id.startsWith("folder:")) return false;
    return true;
  }

  function setChevron(btn: HTMLButtonElement, open: boolean): void {
    setTreeChevronExpanded(btn, open);
  }

  function updateSelected(nodeId: string | null): void {
    container.querySelectorAll(".tree-row.selected").forEach((row) => row.classList.remove("selected"));
    if (!nodeId) return;
    const row = container.querySelector(
      `.tree-node[data-node-id="${CSS.escape(nodeId)}"] > .tree-row`,
    );
    row?.classList.add("selected");
  }

  async function fetchChildren(parentId: string, apiRoot: string): Promise<TreeNode[]> {
    if (!parentId) return virtualRootNodes(allowedRoots);
    if (options.fetchExtraChildren) {
      const extra = await options.fetchExtraChildren(parentId, apiRoot);
      if (extra !== null) return extra;
    }
    if (isVaultRoot(parentId)) {
      let nodes = await corpus.tree(apiRootFromVaultRoot(parentId), "");
      if (options.augmentChildren) {
        nodes = await options.augmentChildren(parentId, apiRoot, nodes);
      }
      return nodes;
    }
    return corpus.tree(apiRoot, parentId);
  }

  async function loadChildren(
    parentEl: HTMLElement,
    parentId: string,
    apiRoot: string,
    depth: number,
  ): Promise<void> {
    const nodes = await fetchChildren(parentId, apiRoot);
    parentEl.innerHTML = "";
    for (const n of nodes) {
      const childApiRoot = isVaultRoot(n.id) ? apiRootFromVaultRoot(n.id) : apiRoot;
      parentEl.appendChild(renderNode(n, depth, childApiRoot));
    }
  }

  async function expandNode(
    nodeId: string,
    childBox: HTMLElement,
    chevron: HTMLButtonElement,
    depth: number,
    apiRoot: string,
  ): Promise<void> {
    expandedIds.add(nodeId);
    saveExpandedToStorage(expandedIds);
    setChevron(chevron, true);
    childBox.classList.remove("hidden");
    if (!childBox.dataset.loaded) {
      childBox.dataset.loaded = "1";
      await loadChildren(childBox, nodeId, apiRoot, depth + 1);
    }
  }

  async function collapseNode(
    nodeId: string,
    childBox: HTMLElement,
    chevron: HTMLButtonElement,
  ): Promise<void> {
    expandedIds.delete(nodeId);
    saveExpandedToStorage(expandedIds);
    setChevron(chevron, false);
    childBox.classList.add("hidden");
  }

  async function toggleExpand(
    n: TreeNode,
    childBox: HTMLElement,
    chevron: HTMLButtonElement,
    depth: number,
    apiRoot: string,
  ): Promise<void> {
    if (expandedIds.has(n.id)) {
      await collapseNode(n.id, childBox, chevron);
    } else {
      await expandNode(n.id, childBox, chevron, depth, apiRoot);
    }
  }

  function renderNode(n: TreeNode, depth: number, apiRoot: string): HTMLElement {
    const nodeWrap = el("div", "tree-node");
    nodeWrap.dataset.nodeId = n.id;
    nodeWrap.dataset.apiRoot = apiRoot;
    nodeWrap.style.setProperty("--tree-depth", String(depth));

    const rowClass =
      n.node_type === "vault_root" ? "tree-row vault-root-row" : "tree-row";
    const row = el("div", rowClass);
    const expandable = isExpandable(n);

    if (expandable) {
      const chevron = createTreeChevron(expandedIds.has(n.id));

      const labelClass =
        n.node_type === "vault_root"
          ? "tree-label vault-root-label"
          : `tree-label ${statusClass(n.status)}`;
      const label = el("span", labelClass, n.label);
      label.title = n.label;
      label.classList.add("tree-label-flex");
      row.append(chevron, label);

      const childBox = el("div", "tree-children");
      const autoExpandPages =
        apiRoot === "import" && n.node_type === "section" && n.label.includes("逐页导入");
      if (autoExpandPages) {
        expandedIds.add(n.id);
        saveExpandedToStorage(expandedIds);
      }
      if (!expandedIds.has(n.id)) childBox.classList.add("hidden");
      nodeWrap.append(row, childBox);

      chevron.onclick = (e) => {
        e.stopPropagation();
        void toggleExpand(n, childBox, chevron, depth, apiRoot);
      };

      options.decorateRow?.(n, row);

      row.onclick = () => {
        updateSelected(n.id);
        if (n.node_type === "vault_root") {
          options.onVaultRootClick?.(apiRootFromVaultRoot(n.id));
        }
        if (isActivatable(n)) options.onActivate?.(n);
        if (!expandedIds.has(n.id)) {
          void expandNode(n.id, childBox, chevron, depth, apiRoot);
        }
      };

      if (expandedIds.has(n.id)) {
        childBox.dataset.loaded = "1";
        void loadChildren(childBox, n.id, apiRoot, depth + 1);
      }
    } else {
      row.appendChild(el("span", "tree-chevron-spacer"));
      const label = el("span", `tree-label leaf ${statusClass(n.status)}`, n.label);
      label.title = n.label;
      label.classList.add("tree-label-flex");
      row.appendChild(label);
      options.decorateRow?.(n, row);
      nodeWrap.appendChild(row);

      row.onclick = () => {
        updateSelected(n.id);
        if (isActivatable(n)) options.onActivate?.(n);
      };
    }

    return nodeWrap;
  }

  async function renderRoots(): Promise<void> {
    container.innerHTML = "";
    const rootsBox = el("div", "tree-roots vault-unified-tree");
    container.appendChild(rootsBox);
    await loadChildren(rootsBox, "", "", 0);
  }

  async function restoreExpanded(): Promise<void> {
    for (const nodeId of [...expandedIds]) {
      const nodeEl = container.querySelector(
        `.tree-node[data-node-id="${CSS.escape(nodeId)}"]`,
      ) as HTMLElement | null;
      if (!nodeEl) continue;
      const chevron = nodeEl.querySelector(".tree-chevron") as HTMLButtonElement | null;
      const childBox = nodeEl.querySelector(".tree-children") as HTMLElement | null;
      if (!chevron || !childBox) continue;
      const depth = parseInt(nodeEl.style.getPropertyValue("--tree-depth") || "0", 10);
      const apiRoot = nodeEl.dataset.apiRoot || "";
      await expandNode(nodeId, childBox, chevron, depth, apiRoot);
    }
  }

  async function reload(): Promise<void> {
    const saved = new Set(expandedIds);
    container.querySelectorAll(".tree-children[data-loaded]").forEach((el) => {
      delete (el as HTMLElement).dataset.loaded;
    });
    for (const id of saved) expandedIds.add(id);
    await renderRoots();
    await restoreExpanded();
  }

  function patchNodeRow(nodeEl: HTMLElement, n: TreeNode): void {
    const label = nodeEl.querySelector(":scope > .tree-row .tree-label") as HTMLElement | null;
    if (!label) return;
    if (label.textContent !== n.label) {
      label.textContent = n.label;
      label.title = n.label;
    }
    const nextStatus = statusClass(n.status);
    for (const c of Array.from(label.classList)) {
      if (c.startsWith("status-")) label.classList.remove(c);
    }
    if (nextStatus) label.classList.add(nextStatus);
  }

  async function refreshChildren(parentId: string): Promise<void> {
    const scrollTop = container.scrollTop;
    const selectedEl = container.querySelector(
      ".tree-row.selected",
    )?.closest(".tree-node") as HTMLElement | null;
    const selectedId = selectedEl?.dataset.nodeId || "";

    let nodeEl = container.querySelector(
      `.tree-node[data-node-id="${CSS.escape(parentId)}"]`,
    ) as HTMLElement | null;

    // 父节点还不在树上：先刷导入根，再试一次
    if (!nodeEl && parentId.startsWith("job:")) {
      await refreshChildren(vaultRootId("import"));
      nodeEl = container.querySelector(
        `.tree-node[data-node-id="${CSS.escape(parentId)}"]`,
      ) as HTMLElement | null;
    }
    if (!nodeEl) return;

    let childBox = nodeEl.querySelector(":scope > .tree-children") as HTMLElement | null;
    let chevron = nodeEl.querySelector(
      ":scope > .tree-row .tree-chevron",
    ) as HTMLButtonElement | null;
    const apiRoot = nodeEl.dataset.apiRoot || "";
    const depth = parseInt(nodeEl.style.getPropertyValue("--tree-depth") || "0", 10);

    // 原为叶子（无展开子树）：先刷父级，让 has_children 更新后重建本节点
    if (!childBox) {
      const grand = nodeEl.parentElement?.closest(".tree-node") as HTMLElement | null;
      const grandId =
        grand?.dataset.nodeId || (parentId.startsWith("job:") ? vaultRootId("import") : "");
      if (grandId && grandId !== parentId) {
        await refreshChildren(grandId);
        nodeEl = container.querySelector(
          `.tree-node[data-node-id="${CSS.escape(parentId)}"]`,
        ) as HTMLElement | null;
        childBox = nodeEl?.querySelector(":scope > .tree-children") as HTMLElement | null;
        chevron = nodeEl?.querySelector(
          ":scope > .tree-row .tree-chevron",
        ) as HTMLButtonElement | null;
      }
    }
    if (!nodeEl || !childBox) {
      container.scrollTop = scrollTop;
      if (selectedId) updateSelected(selectedId);
      return;
    }

    expandedIds.add(parentId);
    saveExpandedToStorage(expandedIds);
    if (chevron) setChevron(chevron, true);
    childBox.classList.remove("hidden");

    const nodes = await fetchChildren(parentId, apiRoot);
    const prev = new Map<string, HTMLElement>();
    for (const child of Array.from(childBox.children)) {
      if (!(child instanceof HTMLElement)) continue;
      const id = child.dataset.nodeId;
      if (id) prev.set(id, child);
    }
    const keep = new Set(nodes.map((n) => n.id));

    for (const n of nodes) {
      const childApiRoot = isVaultRoot(n.id) ? apiRootFromVaultRoot(n.id) : apiRoot;
      let elNode = prev.get(n.id);
      const hadBox = !!elNode?.querySelector(":scope > .tree-children");
      const wantExpand = isExpandable(n);
      if (!elNode || hadBox !== wantExpand) {
        elNode = renderNode(n, depth + 1, childApiRoot);
      } else {
        patchNodeRow(elNode, n);
      }
      childBox.appendChild(elNode); // 已存在则仅重排，不销毁子树
    }
    for (const [id, elNode] of prev) {
      if (!keep.has(id)) elNode.remove();
    }
    childBox.dataset.loaded = "1";

    container.scrollTop = scrollTop;
    if (selectedId && keep.has(selectedId)) {
      updateSelected(selectedId);
    } else if (selectedId) {
      updateSelected(selectedId);
    }
  }

  async function revealPath(apiRoot: string, path: string[]): Promise<boolean> {
    const fullPath = [vaultRootId(apiRoot), ...path];

    async function walk(parentEl: HTMLElement, segments: string[]): Promise<boolean> {
      if (!segments.length) return false;
      const targetId = segments[0];
      const nodeEl = parentEl.querySelector(
        `:scope > .tree-node[data-node-id="${CSS.escape(targetId)}"]`,
      ) as HTMLElement | null;
      if (!nodeEl) return false;

      if (segments.length === 1) {
        highlight(targetId);
        return true;
      }

      const chevron = nodeEl.querySelector(".tree-chevron") as HTMLButtonElement | null;
      const childBox = nodeEl.querySelector(".tree-children") as HTMLElement | null;
      if (!chevron || !childBox) return false;

      const depth = parseInt(nodeEl.style.getPropertyValue("--tree-depth") || "0", 10);
      const nodeApiRoot = nodeEl.dataset.apiRoot || apiRoot;
      await expandNode(targetId, childBox, chevron, depth, nodeApiRoot);
      return walk(childBox, segments.slice(1));
    }

    const rootsBox = container.querySelector(".tree-roots") as HTMLElement | null;
    if (!rootsBox) return false;
    return walk(rootsBox, fullPath);
  }

  async function expandApiRoot(apiRoot: string): Promise<void> {
    const rootId = vaultRootId(apiRoot);
    if (!expandedIds.has(rootId)) expandedIds.add(rootId);
    saveExpandedToStorage(expandedIds);
    const nodeEl = container.querySelector(
      `.tree-node[data-node-id="${CSS.escape(rootId)}"]`,
    ) as HTMLElement | null;
    if (!nodeEl) {
      await renderRoots();
      await restoreExpanded();
      return;
    }
    const chevron = nodeEl.querySelector(".tree-chevron") as HTMLButtonElement | null;
    const childBox = nodeEl.querySelector(".tree-children") as HTMLElement | null;
    if (!chevron || !childBox) return;
    await expandNode(rootId, childBox, chevron, 0, apiRoot);
  }

  function highlight(nodeId: string): void {
    updateSelected(nodeId);
    const row = container.querySelector(
      `.tree-node[data-node-id="${CSS.escape(nodeId)}"] > .tree-row`,
    );
    row?.scrollIntoView({ block: "nearest" });
  }

  void renderRoots().then(() => restoreExpanded());

  return { reload, refreshChildren, revealPath, highlight, expandApiRoot };
}
