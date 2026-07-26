import { corpus } from "../api/corpus";
import type { ContentPack, CorpusNode, LinkEntry } from "../api/types";
import { openKpPicker } from "./KpPicker";
import { formatLinkLabel } from "./ContainerOutline";
import { store } from "../state/store";
import { el } from "../util/dom";
import { openSearchDrawer } from "../views/SearchDrawer";
import { deleteNodeWithConfirm } from "../util/deleteNode";
import {
  gradeLabel,
  isEnglishSubject,
  nodeTypeLabel,
  relTypeLabel,
  statusLabel,
  subjectLabel,
} from "../util/labels";

let contentPacks: ContentPack[] = [];
let showAllLinks = false;

/** 导入模式右栏 AI 对话宿主（MinerU AI 分段导入挂载点）。 */
let _importCopilotHost: HTMLElement | null = null;
let _setInspectorTab: ((tab: "ai" | "props") => void) | null = null;

export function getImportCopilotHost(): HTMLElement | null {
  return _importCopilotHost;
}

/** 打开右栏「AI 对话」页签（启动/恢复会话时调用）。 */
export function showImportCopilotTab(): void {
  _setInspectorTab?.("ai");
}

export async function loadContentPacks(): Promise<void> {
  try {
    contentPacks = await corpus.contentPacks();
  } catch {
    contentPacks = [];
  }
}

export function mountInspector(rootEl: HTMLElement): void {
  loadContentPacks();

  rootEl.classList.add("studio-inspector--tabbed");

  const tabs = el("div", "inspector-tabs");
  const tabAi = el("button", "inspector-tab", "AI 对话") as HTMLButtonElement;
  tabAi.type = "button";
  tabAi.title = "MinerU 分段导入的 Copilot 对话";
  const tabProps = el("button", "inspector-tab inspector-tab--active", "属性") as HTMLButtonElement;
  tabProps.type = "button";
  tabProps.title = "导入流程调试与节点属性";
  tabs.append(tabAi, tabProps);
  rootEl.appendChild(tabs);

  const aiPane = el("div", "inspector-pane inspector-pane--ai hidden");
  _importCopilotHost = el("div", "inspector-copilot-slot");
  _importCopilotHost.appendChild(
    el(
      "div",
      "inspector-copilot-empty muted",
      "在导入面板选择教材方案与 PDF 后，点「AI 分段导入」。刷新后会自动恢复未结束的会话。",
    ),
  );
  aiPane.appendChild(_importCopilotHost);
  rootEl.appendChild(aiPane);

  const propsPane = el("div", "inspector-pane inspector-pane--props");
  const workflowHost = el("div", "inspector-workflow-slot");
  propsPane.appendChild(workflowHost);
  void import("./import/ImportWorkflowInspector").then(({ mountImportWorkflowInspector }) => {
    mountImportWorkflowInspector(workflowHost);
  });
  const nodeHost = el("div", "inspector-node-slot");
  propsPane.appendChild(nodeHost);
  rootEl.appendChild(propsPane);

  const setTab = (tab: "ai" | "props"): void => {
    const ai = tab === "ai";
    tabAi.classList.toggle("inspector-tab--active", ai);
    tabProps.classList.toggle("inspector-tab--active", !ai);
    aiPane.classList.toggle("hidden", !ai);
    propsPane.classList.toggle("hidden", ai);
    rootEl.classList.toggle("studio-inspector--ai", ai);
    rootEl.closest(".studio-right-dock")?.classList.toggle("studio-right-dock--ai", ai);
    try {
      window.localStorage.setItem("studio.inspector.tab", tab);
    } catch {
      /* ignore */
    }
    document.dispatchEvent(new CustomEvent("studio:inspector-tab", { detail: { tab } }));
  };
  _setInspectorTab = setTab;
  tabAi.onclick = () => {
    setTab("ai");
    // 点「AI 对话」页签：左栏任务 / 中栏预览跟随当前对话绑定的导入任务
    const jobId = _importCopilotHost?.getAttribute("data-copilot-job") || "";
    if (jobId) {
      store.focusImportWorkspace();
      store.showImportPreview();
      void import("./import/activeMinerUJob").then(({ focusMinerUJobTriPane }) => {
        focusMinerUJobTriPane(jobId);
      });
    }
  };
  tabProps.onclick = () => setTab("props");

  try {
    const saved = window.localStorage.getItem("studio.inspector.tab");
    if (saved === "ai" || saved === "props") setTab(saved);
  } catch {
    /* ignore */
  }

  let renderedSig = "";
  let renderSeq = 0;

  store.subscribe(() => void render());
  void render();

  async function render(): Promise<void> {
    const node = store.currentNode;
    if (!node) {
      renderedSig = "";
      nodeHost.innerHTML = "";
      if (!(store.importMode || store.activeRibbon === "import")) {
        nodeHost.appendChild(el("p", "muted", "属性面板"));
      }
      return;
    }
    const sig = `${node.id}|${node.status}|${node.vector_indexed}|${showAllLinks}`;
    // Skip full rebuild when only unrelated store noise (e.g. import poll) fired.
    if (sig === renderedSig && nodeHost.childElementCount > 0) {
      return;
    }
    renderedSig = sig;
    const seq = ++renderSeq;
    nodeHost.innerHTML = "";

    nodeHost.appendChild(el("h3", "inspector-title", "属性"));

    const statusRow = el("div", "inspector-row");
    statusRow.appendChild(el("label", "", "状态"));
    const statusSel = document.createElement("select");
    for (const s of ["draft", "review", "archived", "rejected"]) {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = statusLabel(s);
      if (s === node.status) opt.selected = true;
      statusSel.appendChild(opt);
    }
    statusSel.onchange = async () => {
      await corpus.setStatus(node.id, statusSel.value);
      await store.openNode(node.id);
    };
    statusRow.appendChild(statusSel);
    nodeHost.appendChild(statusRow);

    nodeHost.appendChild(field("类型", nodeTypeLabel(node.node_type)));
    nodeHost.appendChild(
      field("学科", `${subjectLabel(node.subject)} / ${gradeLabel(node.grade_band)}`),
    );

    const localeHint = isEnglishSubject(node.subject)
        ? "入库语言：英文（英语学科专用）"
        : "入库语言：中文 + 学科符号（公式/化学式等）";
    nodeHost.appendChild(el("p", "muted locale-hint", localeHint));

    const warnings = node.meta.locale_warnings;
    if (Array.isArray(warnings) && warnings.length) {
      const warnBox = el("div", "locale-warnings");
      warnBox.appendChild(el("strong", "", "语言校验提示"));
      const ul = document.createElement("ul");
      for (const w of warnings) {
        const li = document.createElement("li");
        li.textContent = String(w);
        ul.appendChild(li);
      }
      warnBox.appendChild(ul);
      nodeHost.appendChild(warnBox);
    }

    const indexRow = el("div", "inspector-row");
    indexRow.appendChild(el("label", "", "检索库"));
    indexRow.appendChild(
      el(
        "span",
        node.vector_indexed ? "" : "health-warn",
        node.vector_indexed ? "✓ 已进检索库" : "✗ 搜不到",
      ),
    );
    nodeHost.appendChild(indexRow);

    if (!node.vector_indexed && node.status === "published") {
      const indexActions = el("div", "inspector-actions index-cta");
      const indexOne = el("button", "btn-primary", "补进检索库");
      indexOne.type = "button";
      indexOne.onclick = async () => {
        await corpus.publish(node.id);
        await store.openNode(node.id);
      };
      indexActions.appendChild(indexOne);
      nodeHost.appendChild(indexActions);
    }

    if (node.status === "draft") {
      const reviewCta = el("div", "inspector-actions");
      const submitBtn = el("button", "btn-primary", "提交审核");
      submitBtn.type = "button";
      submitBtn.onclick = async () => {
        try {
          await corpus.submitReview(node.id);
          await store.openNode(node.id);
          document.dispatchEvent(new CustomEvent("studio:workqueue-refresh"));
        } catch (err) {
          alert(`提交审核失败：${err instanceof Error ? err.message : String(err)}`);
        }
      };
      reviewCta.appendChild(submitBtn);
      nodeHost.appendChild(reviewCta);
    }

    const kpRow = el("div", "inspector-row");
    kpRow.appendChild(el("label", "", "主考纲"));
    const kpVal = String((node.links as Record<string, string>).primary_kp || node.meta.kp_id || "—");
    kpRow.appendChild(el("span", "kp-display", kpVal));
    const kpPick = el("button", "", "选择…");
    kpPick.type = "button";
    kpPick.onclick = () => openKpPicker(node, () => store.openNode(node.id));
    kpRow.appendChild(kpPick);
    nodeHost.appendChild(kpRow);

    if (contentPacks.length) {
      const packRow = el("div", "inspector-row");
      packRow.appendChild(el("label", "", "内容包"));
      const packSel = document.createElement("select");
      const empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "—";
      packSel.appendChild(empty);
      for (const p of contentPacks) {
        const opt = document.createElement("option");
        opt.value = p.pack_id;
        opt.textContent = p.label;
        if (node.meta.content_pack_id === p.pack_id) opt.selected = true;
        packSel.appendChild(opt);
      }
      packSel.onchange = async () => {
        await corpus.patchNode(node.id, {
          meta: { ...node.meta, content_pack_id: packSel.value },
        } as Partial<CorpusNode>);
        await store.openNode(node.id);
      };
      packRow.appendChild(packSel);
      nodeHost.appendChild(packRow);
    }

    const links = await corpus.nodeLinks(node.id, showAllLinks ? "" : "example_for,references_chunk,examined_by");
    if (seq !== renderSeq) return;

    const toggleRow = el("div", "inspector-row");
    const toggleAll = el("button", "btn-sm", showAllLinks ? "隐藏噪声边" : "显示全部边");
    toggleAll.type = "button";
    toggleAll.onclick = () => {
      showAllLinks = !showAllLinks;
      render();
    };
    toggleRow.appendChild(toggleAll);
    if (links.hidden_count > 0 && !showAllLinks) {
      toggleRow.appendChild(el("span", "muted", `已隐藏 ${links.hidden_count} 条`));
    }
    nodeHost.appendChild(toggleRow);

    appendLinkSection(nodeHost, "结构", links.structural);
    appendLinkSection(nodeHost, "考纲", links.kp);
    appendLinkSection(nodeHost, "标签", links.tags);
    if (links.other.length) appendLinkSection(nodeHost, "其他", links.other, true);

    nodeHost.appendChild(el("h4", "", "入链"));
    const inUl = el("ul", "link-list");
    links.inlinks.forEach((l) => inUl.appendChild(linkItem(l)));
    nodeHost.appendChild(inUl);

    const actions = el("div", "inspector-actions");
    const staging = el("button", "", "试搜（暂存库）");
    staging.type = "button";
    staging.onclick = async () => {
      await corpus.embedStaging(node.id);
      openSearchDrawer(node.summary || node.label, node.id);
    };
    if (node.status === "review") {
      const publish = el("button", "btn-primary", "正式发布");
      publish.type = "button";
      publish.onclick = async () => {
        try {
          await corpus.publish(node.id);
          await store.openNode(node.id);
          document.dispatchEvent(new CustomEvent("studio:workqueue-refresh"));
        } catch (err) {
          alert(`发布失败：${err instanceof Error ? err.message : String(err)}`);
        }
      };
      actions.appendChild(publish);
    }
    const graphBtn = el("button", "", "结构图谱");
    graphBtn.type = "button";
    graphBtn.onclick = () => {
      document.dispatchEvent(new CustomEvent("studio:structural-graph"));
      store.toggleGraph(true);
    };
    actions.appendChild(graphBtn);

    const isContainer = node.node_type === "textbook" || node.node_type === "section";
    const deletable = ["draft", "review", "rejected", "archived", "published"].includes(node.status);
    if (deletable) {
      const del = el("button", "btn-danger", isContainer ? "删除整棵子树" : "删除");
      del.type = "button";
      del.onclick = async () => {
        const ok = await deleteNodeWithConfirm(node, { cascade: isContainer });
        if (ok) nodeHost.innerHTML = "";
      };
      actions.appendChild(del);
    }
    nodeHost.appendChild(actions);

    const searchRow = el("div", "inspector-row");
    const sq = document.createElement("input");
    sq.placeholder = "输入检索词…";
    const searchBtn = el("button", "", "检索");
    searchBtn.type = "button";
    searchBtn.onclick = () => openSearchDrawer(sq.value || node.summary, node.id);
    searchRow.append(sq, searchBtn);
    nodeHost.appendChild(searchRow);
  }
}

function appendLinkSection(
  root: HTMLElement,
  title: string,
  items: LinkEntry[],
  collapsible = false,
): void {
  if (!items.length) return;
  if (collapsible) {
    const det = el("details", "link-section");
    det.appendChild(el("summary", "", `${title} (${items.length})`));
    const ul = el("ul", "link-list");
    items.forEach((l) => ul.appendChild(linkItem(l)));
    det.appendChild(ul);
    root.appendChild(det);
  } else {
    root.appendChild(el("h4", "", `${title} (${items.length})`));
    const ul = el("ul", "link-list");
    items.forEach((l) => ul.appendChild(linkItem(l)));
    root.appendChild(ul);
  }
}

function field(label: string, value: string): HTMLElement {
  const row = el("div", "inspector-row");
  row.appendChild(el("label", "", label));
  row.appendChild(el("span", "", value));
  return row;
}

function linkItem(l: LinkEntry): HTMLElement {
  const li = el("li", "");
  const display = formatLinkLabel(l.label, l.node_id);
  const a = el("a", "link-item", display);
  a.href = "#";
  a.title = relTypeLabel(l.rel_type);
  a.onclick = (e) => {
    e.preventDefault();
    store.openNode(l.node_id);
  };
  li.appendChild(a);
  li.appendChild(el("span", "rel-tag", relTypeLabel(l.rel_type)));
  return li;
}
