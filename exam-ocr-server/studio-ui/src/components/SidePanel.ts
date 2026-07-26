import { corpus } from "../api/corpus";
import type { CorpusNode, TreeNode } from "../api/types";
import { store, type RibbonSection } from "../state/store";
import { el } from "../util/dom";
import { mountSearchPanel, focusSearchPanel } from "../views/SearchPanel";
import { mountWorkQueuePanel } from "../views/WorkQueue";
import { mountVaultTree, VAULT_ROOT_PREFIX, type VaultTreeHandle } from "./VaultTree";
import {
  activateB35TreeNode,
  augmentImportRootChildren,
  bindB35ImportTree,
  decorateB35TreeRow,
  fetchB35JobChildren,
  initB35ImportTreeListeners,
} from "./import/B35ImportTree";
import {
  bindMinerUImportTree,
  decorateMinerUTreeRow,
  initMinerUImportTreeListeners,
} from "./import/MinerUImportTree";

const TEXTBOOK_ROOTS = ["textbook", "kp", "method", "shortcut", "question"];
const IMPORT_ROOTS = ["import"];

/** Textbook top-level row: re-run structure-merge (chapters) only — no publish. */
function decorateTextbookTreeRow(n: TreeNode, row: HTMLElement): void {
  if (n.node_type !== "textbook") return;
  if (row.querySelector(".tree-row-actions--textbook-regen")) return;

  const actions = el(
    "div",
    "tree-row-actions tree-row-actions--always tree-row-actions--textbook-regen",
  );
  const btn = el("button", "tree-row-action", "重新生成章节") as HTMLButtonElement;
  btn.type = "button";
  btn.title = "按现有 IR 重建章节目录（保留逐页导入，不自动发布）";
  btn.onclick = (e) => {
    e.stopPropagation();
    void regenerateTextbookChapters(n, btn);
  };
  actions.appendChild(btn);
  row.appendChild(actions);
}

async function resolveImportJobId(n: TreeNode): Promise<string> {
  const fromMeta = String(n.meta?.import_job_id || "").trim();
  if (fromMeta) return fromMeta;
  try {
    const full = await corpus.getNode(n.id);
    return String(full.meta?.import_job_id || "").trim();
  } catch {
    return "";
  }
}

async function regenerateTextbookChapters(
  n: TreeNode,
  btn: HTMLButtonElement,
): Promise<void> {
  const ok = window.confirm(
    `重新生成「${n.label}」的章节目录？\n\n` +
      "将按现有解析结果重建章/节/目录（保留逐页导入），不会自动发布进检索库。",
  );
  if (!ok) return;

  const jobId = await resolveImportJobId(n);
  if (!jobId) {
    window.alert("该教材没有关联的导入任务，无法重新生成章节。");
    return;
  }

  const prev = btn.textContent || "重新生成章节";
  btn.disabled = true;
  btn.textContent = "重建中…";
  try {
    await corpus.structureMerge(jobId, { purge: true });
    await textbookTree?.reload();
    if (store.currentNode?.id === n.id || store.currentNode?.meta?.import_job_id === jobId) {
      await store.openNode(store.currentNode?.id || n.id);
    }
  } catch (err) {
    window.alert(
      `重新生成章节失败：${err instanceof Error ? err.message : String(err)}`,
    );
  } finally {
    btn.disabled = false;
    btn.textContent = prev;
  }
}

let panelBody: HTMLElement | null = null;
let textbookSection: HTMLElement | null = null;
let textbookTreePanel: HTMLElement | null = null;
let importSection: HTMLElement | null = null;
let importTreePanel: HTMLElement | null = null;
let searchPanel: HTMLElement | null = null;
let todoPanel: HTMLElement | null = null;

let textbookTree: VaultTreeHandle | null = null;
let importTree: VaultTreeHandle | null = null;
let activeVaultTree: VaultTreeHandle | null = null;
let currentSection: RibbonSection = store.activeRibbon;

async function openImportJob(jobId: string, pageNo?: number): Promise<void> {
  const id = String(jobId || "").trim();
  if (!id) return;
  store.focusImportWorkspace();
  store.showImportPreview();
  const { focusMinerUJobTriPane } = await import("./import/activeMinerUJob");
  focusMinerUJobTriPane(id, pageNo && pageNo > 0 ? pageNo : undefined);
  try {
    const job = await corpus.importJob(id);
    store.setImportJob(job);
  } catch (e) {
    console.error(e);
  }
}

function parsePageNoFromNodeId(id: string): number {
  const m = id.match(/:p(\d{4})$/);
  return m ? Number.parseInt(m[1], 10) : 0;
}

/** Import-tree page chunk → MinerU preview ops (not textbook editor). */
function looksLikeMinerUImportPage(n: TreeNode): boolean {
  if (n.node_type === "import_job" || n.node_type === "inbox_item") return false;
  const meta = n.meta || {};
  if (meta.chunk_kind === "page_import") return true;
  if (Number(meta.source_page) > 0 && meta.import_job_id) return true;
  if (n.node_type === "chunk" && parsePageNoFromNodeId(n.id) > 0) return true;
  return false;
}

async function openMinerUImportPage(n: TreeNode): Promise<void> {
  let jobId = String((n.meta || {}).import_job_id || "").trim();
  let pageNo = Number((n.meta || {}).source_page || 0) || parsePageNoFromNodeId(n.id);
  if (!jobId || !pageNo) {
    try {
      const node = await corpus.getNode(n.id);
      jobId = jobId || String(node.meta?.import_job_id || "").trim();
      pageNo = pageNo || Number(node.meta?.source_page || 0) || parsePageNoFromNodeId(n.id);
    } catch (e) {
      console.error(e);
    }
  }
  if (!jobId) {
    // Last resort: stay in import preview of current job if any
    const current = store.activeImportJob?.id;
    if (current && pageNo > 0) {
      void openImportJob(current, pageNo);
      return;
    }
    console.warn("import page missing import_job_id", n.id);
    return;
  }
  importTree?.highlight(n.id);
  store.markSelected(n.id);
  void openImportJob(jobId, pageNo > 0 ? pageNo : undefined);
}

function onNodeActivate(n: TreeNode): void {
  if (activateB35TreeNode(n)) return;
  if (n.node_type === "import_job") {
    const jobId = n.id.replace("job:", "");
    void openImportJob(jobId);
    return;
  }
  if (n.node_type === "inbox_item") {
    const itemId = n.id.replace("inbox-item:", "");
    corpus.inbox().then((items) => {
      const item = items.find((i) => i.id === itemId);
      if (item?.proposed_node_ids[0]) store.openNode(item.proposed_node_ids[0]);
    });
    return;
  }
  // Import ribbon: page clicks stay in MinerU preview (reprocess / feedback / debug).
  if (store.activeRibbon === "import" && looksLikeMinerUImportPage(n)) {
    void openMinerUImportPage(n);
    return;
  }
  store.openNode(n.id);
}

export function rootForNodeId(nodeId: string, node?: CorpusNode): string {
  if (nodeId.startsWith("job:")) return "import";
  if (nodeId.startsWith("inbox-item:") || nodeId.startsWith("inbox:")) return "inbox";
  if (nodeId.startsWith("folder:")) {
    if (nodeId.includes(":method:")) return "method";
    if (nodeId.includes(":shortcut:")) return "shortcut";
    if (nodeId.includes(":question:")) return "question";
  }
  if (node) {
    const t = node.node_type;
    if (t === "kp") return "kp";
    if (t === "method") return "method";
    if (t === "shortcut") return "shortcut";
    if (t === "question") return "question";
    if (t === "import_job") return "import";
    if (t === "inbox_item") return "inbox";
  }
  if (nodeId.startsWith("kw:")) return "kp";
  return "textbook";
}

export function ribbonForApiRoot(apiRoot: string): RibbonSection {
  if (apiRoot === "import") return "import";
  if (apiRoot === "inbox") return "todo";
  return "textbook";
}

function buildRevealPath(nodeId: string, node: CorpusNode): string[] {
  const links = node.links || {};
  const path: string[] = [];
  if (links.textbook_id) path.push(String(links.textbook_id));
  if (links.section_id && !path.includes(String(links.section_id))) {
    path.push(String(links.section_id));
  }
  if (!path.includes(nodeId)) path.push(nodeId);
  return path;
}

function vaultTreeForRoot(apiRoot: string): VaultTreeHandle | null {
  const ribbon = ribbonForApiRoot(apiRoot);
  if (ribbon === "import") return importTree;
  if (ribbon === "textbook") return textbookTree;
  return null;
}

export async function revealInTree(nodeId: string): Promise<void> {
  let apiRoot: string;
  let path: string[] = [nodeId];

  try {
    const node = await corpus.getNode(nodeId);
    apiRoot = rootForNodeId(nodeId, node);
    path = buildRevealPath(nodeId, node);
  } catch {
    apiRoot = rootForNodeId(nodeId);
  }

  const ribbon = ribbonForApiRoot(apiRoot);
  // Stay in import workspace — never steal focus to textbook while importing.
  if (store.activeRibbon === "import" && ribbon !== "import") {
    return;
  }
  if (ribbon !== store.activeRibbon) {
    store.setActiveRibbon(ribbon);
  }

  const tree = vaultTreeForRoot(apiRoot);
  if (!tree) return;

  activeVaultTree = tree;
  await tree.expandApiRoot(apiRoot);
  const found = await tree.revealPath(apiRoot, path);
  if (!found) tree.highlight(nodeId);
}

export function refreshVault(): Promise<void> {
  return Promise.all([textbookTree?.reload(), importTree?.reload()].filter(Boolean)).then(
    () => undefined,
  );
}

let editionOpsPanel: HTMLElement | null = null;

function showSection(section: RibbonSection): void {
  if (!panelBody) return;
  currentSection = section;

  const views: { id: RibbonSection; el: HTMLElement | null }[] = [
    { id: "textbook", el: textbookSection },
    { id: "import", el: importSection },
    { id: "search", el: searchPanel },
    { id: "todo", el: todoPanel },
    { id: "edition-ops", el: editionOpsPanel },
  ];
  for (const v of views) {
    v.el?.classList.toggle("hidden", v.id !== section);
  }

  if (section === "textbook") activeVaultTree = textbookTree;
  else if (section === "import") {
    activeVaultTree = importTree;
    void importTree?.expandApiRoot("import");
  } else activeVaultTree = null;

  if (section === "search") focusSearchPanel();
  if (section === "edition-ops") {
    document.dispatchEvent(new CustomEvent("studio:edition-ops-loaded"));
  }
}

export function switchSidePanel(section: RibbonSection): void {
  showSection(section);
}

export function mountSidePanel(rootEl: HTMLElement): void {
  rootEl.className = "studio-side-panel";

  panelBody = el("div", "side-panel-body");
  rootEl.appendChild(panelBody);

  // Textbook tree (hint outside tree — VaultTree clears its container)
  textbookSection = el("div", "side-panel-section");
  textbookSection.appendChild(
    el(
      "p",
      "muted side-hint",
      "定稿与发布在这里：改内容 → 提交审核 → 正式发布。",
    ),
  );
  textbookTreePanel = el("div", "vault-tree-panel");
  textbookSection.appendChild(textbookTreePanel);
  panelBody.appendChild(textbookSection);
  textbookTree = mountVaultTree(textbookTreePanel, {
    onActivate: onNodeActivate,
    roots: TEXTBOOK_ROOTS,
    decorateRow: decorateTextbookTreeRow,
  });

  // Import section: action + tree
  importSection = el("div", "side-panel-section import-panel hidden");
  importSection.appendChild(
    el(
      "p",
      "muted side-hint",
      "预览校对用：改页效果看这里；定稿发布请到「教材」树。",
    ),
  );
  const importActions = el("div", "import-actions-row");
  const importBtn = el("button", "vault-action-btn", "+ 新建 PDF 导入");
  importBtn.type = "button";
  importBtn.onclick = () => store.enterImportMode();
  const mineruBtn = el("button", "vault-action-btn secondary", "解析设置");
  mineruBtn.type = "button";
  mineruBtn.onclick = () => store.showMinerUSettings();
  importActions.append(importBtn, mineruBtn);
  importSection.appendChild(importActions);
  importTreePanel = el("div", "vault-tree-panel");
  importSection.appendChild(importTreePanel);
  panelBody.appendChild(importSection);
  initB35ImportTreeListeners();
  initMinerUImportTreeListeners();
  importTree = mountVaultTree(importTreePanel, {
    onActivate: onNodeActivate,
    roots: IMPORT_ROOTS,
    augmentChildren: async (parentId, apiRoot, nodes) => {
      if (apiRoot === "import" && parentId === `${VAULT_ROOT_PREFIX}import`) {
        return augmentImportRootChildren(nodes);
      }
      return nodes;
    },
    fetchExtraChildren: fetchB35JobChildren,
    decorateRow: (n, row) => {
      decorateB35TreeRow(n, row);
      decorateMinerUTreeRow(n, row);
    },
    onVaultRootClick: (apiRoot) => {
      if (apiRoot === "import") store.showImportHub();
    },
  });
  bindB35ImportTree(importTree);
  bindMinerUImportTree(importTree);

  // Search panel
  searchPanel = el("div", "side-panel-section search-panel hidden");
  panelBody.appendChild(searchPanel);
  mountSearchPanel(searchPanel);

  // Todo panel
  todoPanel = el("div", "side-panel-section hidden");
  panelBody.appendChild(todoPanel);
  mountWorkQueuePanel(todoPanel);

  // Edition ops: left = scheme nav · center = detail · right = edit
  editionOpsPanel = el("div", "side-panel-section hidden");
  editionOpsPanel.appendChild(el("h3", "", "方案"));
  editionOpsPanel.appendChild(el("p", "muted side-hint", "点选后中间编辑，右侧看字段说明"));
  const editionList = el("div", "edition-side-list");
  editionList.textContent = "加载中…";
  editionOpsPanel.appendChild(editionList);
  const newEditionBtn = el("button", "btn-secondary edition-side-new", "＋ 新建方案") as HTMLButtonElement;
  newEditionBtn.type = "button";
  newEditionBtn.onclick = () => {
    document.dispatchEvent(new CustomEvent("studio:edition-ops-open-create"));
  };
  editionOpsPanel.appendChild(newEditionBtn);
  panelBody.appendChild(editionOpsPanel);

  const refreshEditionSideList = (): void => {
    void Promise.all([
      import("../api/editionOps"),
      import("../views/EditionOpsPanel"),
    ])
      .then(([{ editionOps }, { getPreferredEditionId, selectEditionInOps }]) =>
        editionOps.list().then(({ editions }) => {
          const selected = getPreferredEditionId();
          editionList.innerHTML = "";
          if (!editions.length) {
            editionList.appendChild(el("p", "muted", "暂无方案"));
            return;
          }
          for (const e of editions) {
            const btn = el("button", "edition-side-item") as HTMLButtonElement;
            btn.type = "button";
            btn.title = e.id;
            btn.classList.toggle("active", e.id === selected);
            btn.appendChild(el("span", "edition-side-name", e.display_name || e.id));
            const st =
              e.published_version
                ? `已发布 ${e.published_version}`
                : e.status === "seed"
                  ? "内置"
                  : e.status === "draft"
                    ? "草稿"
                    : e.status || "";
            btn.appendChild(el("span", "edition-side-meta", st));
            btn.onclick = () => selectEditionInOps(e.id);
            editionList.appendChild(btn);
          }
        }),
      )
      .catch(() => {
        editionList.innerHTML = "";
        editionList.appendChild(el("p", "muted", "方案列表加载失败"));
      });
  };
  refreshEditionSideList();
  document.addEventListener("studio:edition-ops-loaded", () => refreshEditionSideList());
  document.addEventListener("studio:edition-ops-select", () => refreshEditionSideList());
  document.addEventListener("studio:edition-ops-saved", () => refreshEditionSideList());

  const healthSlot = el("div", "index-health-slot");
  rootEl.appendChild(healthSlot);
  document.dispatchEvent(new CustomEvent("studio:mount-index-health", { detail: healthSlot }));

  document.addEventListener("studio:reveal-node", ((e: CustomEvent) => {
    revealInTree(e.detail.nodeId).catch(console.error);
  }) as EventListener);

  document.addEventListener("studio:tree-highlight", ((e: CustomEvent<{ nodeId: string }>) => {
    const nodeId = e.detail?.nodeId;
    if (!nodeId || !activeVaultTree) return;
    activeVaultTree.highlight(nodeId);
  }) as EventListener);

  store.subscribe(() => {
    if (store.activeRibbon !== currentSection) {
      showSection(store.activeRibbon);
    }
    if (store.selectedNodeId && activeVaultTree) {
      activeVaultTree.highlight(store.selectedNodeId);
    }
  });

  showSection(store.activeRibbon);
}
