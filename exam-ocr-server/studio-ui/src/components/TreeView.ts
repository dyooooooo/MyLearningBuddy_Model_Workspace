import { corpus } from "../api/corpus";
import type { TreeNode } from "../api/types";
import { el, statusClass } from "../util/dom";
import { createTreeChevron, setTreeChevronExpanded } from "../util/treeChevron";

export type TreeViewMode = "navigate" | "select";

export interface TreeViewOptions {
  root: string;
  mode?: TreeViewMode;
  onActivate?: (node: TreeNode) => void;
  onSelect?: (node: TreeNode) => void;
  isActivatable?: (node: TreeNode) => boolean;
}

export interface TreeViewHandle {
  root: string;
  setRoot: (root: string) => Promise<void>;
  reload: () => Promise<void>;
  revealPath: (path: string[]) => Promise<boolean>;
  highlight: (nodeId: string) => void;
  getExpandedIds: () => ReadonlySet<string>;
}

export function mountTreeView(
  container: HTMLElement,
  options: TreeViewOptions,
): TreeViewHandle {
  const expandedIds = new Set<string>();
  let currentRoot = options.root;
  const mode = options.mode ?? "navigate";

  function isExpandable(n: TreeNode): boolean {
    return n.has_children || n.id.startsWith("folder:");
  }

  function isActivatable(n: TreeNode): boolean {
    if (options.isActivatable) return options.isActivatable(n);
    if (mode === "select") return n.node_type === "kp";
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

  async function loadChildren(
    parentEl: HTMLElement,
    parentId: string,
    depth: number,
  ): Promise<void> {
    const nodes = await corpus.tree(currentRoot, parentId);
    parentEl.innerHTML = "";
    for (const n of nodes) {
      parentEl.appendChild(renderNode(n, depth));
    }
  }

  async function expandNode(
    nodeId: string,
    childBox: HTMLElement,
    chevron: HTMLButtonElement,
    depth: number,
  ): Promise<void> {
    expandedIds.add(nodeId);
    setChevron(chevron, true);
    childBox.classList.remove("hidden");
    if (!childBox.dataset.loaded) {
      childBox.dataset.loaded = "1";
      await loadChildren(childBox, nodeId, depth + 1);
    }
  }

  async function collapseNode(
    nodeId: string,
    childBox: HTMLElement,
    chevron: HTMLButtonElement,
  ): Promise<void> {
    expandedIds.delete(nodeId);
    setChevron(chevron, false);
    childBox.classList.add("hidden");
  }

  async function toggleExpand(
    n: TreeNode,
    childBox: HTMLElement,
    chevron: HTMLButtonElement,
    depth: number,
  ): Promise<void> {
    if (expandedIds.has(n.id)) {
      await collapseNode(n.id, childBox, chevron);
    } else {
      await expandNode(n.id, childBox, chevron, depth);
    }
  }

  function activateNode(n: TreeNode): void {
    if (mode === "select" && n.node_type === "kp") {
      options.onSelect?.(n);
      return;
    }
    options.onActivate?.(n);
  }

  function renderNode(n: TreeNode, depth: number): HTMLElement {
    const nodeWrap = el("div", "tree-node");
    nodeWrap.dataset.nodeId = n.id;
    nodeWrap.style.setProperty("--tree-depth", String(depth));

    const row = el("div", "tree-row");
    const expandable = isExpandable(n);

    if (expandable) {
      const chevron = createTreeChevron(expandedIds.has(n.id));

      const label = el("span", `tree-label ${statusClass(n.status)}`, n.label);
      label.title = n.label;
      row.append(chevron, label);

      const childBox = el("div", "tree-children");
      if (!expandedIds.has(n.id)) childBox.classList.add("hidden");
      nodeWrap.append(row, childBox);

      chevron.onclick = (e) => {
        e.stopPropagation();
        void toggleExpand(n, childBox, chevron, depth);
      };

      row.onclick = () => {
        updateSelected(n.id);
        if (isActivatable(n)) activateNode(n);
        if (!expandedIds.has(n.id)) {
          void expandNode(n.id, childBox, chevron, depth);
        }
      };

      if (expandedIds.has(n.id)) {
        childBox.dataset.loaded = "1";
        void loadChildren(childBox, n.id, depth + 1);
      }
    } else {
      row.appendChild(el("span", "tree-chevron-spacer"));
      const label = el("span", `tree-label leaf ${statusClass(n.status)}`, n.label);
      label.title = n.label;
      row.appendChild(label);
      nodeWrap.appendChild(row);

      row.onclick = () => {
        updateSelected(n.id);
        if (isActivatable(n)) activateNode(n);
      };
    }

    return nodeWrap;
  }

  async function renderRoots(): Promise<void> {
    container.innerHTML = "";
    const rootsBox = el("div", "tree-roots");
    container.appendChild(rootsBox);
    await loadChildren(rootsBox, "", 0);
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
      await expandNode(nodeId, childBox, chevron, depth);
    }
  }

  async function reload(): Promise<void> {
    const saved = new Set(expandedIds);
    expandedIds.clear();
    for (const id of saved) expandedIds.add(id);
    await renderRoots();
    await restoreExpanded();
  }

  async function revealPath(path: string[]): Promise<boolean> {
    if (!path.length) return false;

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
      await expandNode(targetId, childBox, chevron, depth);
      return walk(childBox, segments.slice(1));
    }

    const rootsBox = container.querySelector(".tree-roots") as HTMLElement | null;
    if (!rootsBox) return false;
    return walk(rootsBox, path);
  }

  function highlight(nodeId: string): void {
    updateSelected(nodeId);
    const row = container.querySelector(
      `.tree-node[data-node-id="${CSS.escape(nodeId)}"] > .tree-row`,
    );
    row?.scrollIntoView({ block: "nearest" });
  }

  async function setRoot(root: string): Promise<void> {
    if (root === currentRoot && container.querySelector(".tree-roots")) return;
    currentRoot = root;
    expandedIds.clear();
    await renderRoots();
  }

  void renderRoots();

  return {
    get root() {
      return currentRoot;
    },
    setRoot,
    reload,
    revealPath,
    highlight,
    getExpandedIds: () => expandedIds,
  };
}
