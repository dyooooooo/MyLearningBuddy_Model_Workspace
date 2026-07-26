import { mountCommandPalette } from "./components/CommandPalette";
import { mountGraphPanel } from "./components/GraphPanel";
import { mountImportBanner } from "./components/ImportBanner";
import { mountImportPipelinePanel } from "./components/ImportPipelinePanel";
import { mountAiTerminal } from "./components/AiTerminal";
import { initLayoutShell } from "./components/LayoutShell";
import { mountInspector } from "./components/Inspector";
import { mountNoteEditor } from "./components/NoteEditor";
import { mountResizeHandle } from "./components/ResizeHandle";
import { mountSidePanel } from "./components/SidePanel";
import { mountStudioRibbon } from "./components/StudioRibbon";
import { mountWorkflowStrip } from "./components/WorkflowStrip";
import { mountImportWizard } from "./views/ImportWizard";
import { mountIndexHealth } from "./views/IndexHealth";
import { mountMinerUSettings } from "./views/MinerUSettings";
import { mountEditionOpsInspector } from "./views/EditionOpsInspector";
import { mountEditionOpsPanel } from "./views/EditionOpsPanel";
import { store } from "./state/store";
import { el } from "./util/dom";

export function mountApp(root: HTMLElement): void {
  root.innerHTML = "";
  root.className = "studio-app";

  const header = el("header", "studio-header");
  const headerRow = el("div", "studio-header-row");
  const titleBlock = el("div", "studio-header-titles");
  titleBlock.appendChild(el("h1", "", "语料库工作台"));
  const subtitle = el("p", "studio-sub", "导入 PDF → 校对定稿 → 发布进检索库");
  titleBlock.appendChild(subtitle);
  headerRow.appendChild(titleBlock);
  const workflowHost = el("div", "studio-workflow-host");
  mountWorkflowStrip(workflowHost);
  headerRow.appendChild(workflowHost);
  const settingsBtn = el("button", "btn-secondary studio-settings-btn", "解析设置");
  settingsBtn.type = "button";
  settingsBtn.title = "配置 MinerU 精准解析 API Token";
  settingsBtn.onclick = () => store.showMinerUSettings();
  headerRow.appendChild(settingsBtn);
  header.appendChild(headerRow);
  root.appendChild(header);

  const layout = el("div", "studio-layout");

  const sidebar = el("aside", "studio-sidebar");
  mountStudioRibbon(sidebar);
  const sidePanelWrap = el("div", "studio-side-panel-wrap");
  const sidePanel = el("div", "studio-side-panel");
  mountSidePanel(sidePanel);
  const leftResize = mountResizeHandle({
    panel: sidePanel,
    edge: "right",
    storageKey: "studio-sidebar-w",
    cssVar: "--sidebar-w",
  });
  const leftEdge = el("div", "studio-panel-edge studio-panel-edge--left");
  leftEdge.appendChild(leftResize.el);
  sidePanelWrap.appendChild(sidePanel);
  sidePanelWrap.appendChild(leftEdge);
  sidebar.appendChild(sidePanelWrap);
  layout.appendChild(sidebar);

  const main = el("main", "studio-main");
  // Center workspace: ribbon section drives which panel is visible.
  //   textbook | search | todo → NoteEditor (node content)
  //   import                   → ImportPipelinePanel (+ NoteEditor when a page chunk is open)
  //   edition-ops              → EditionOpsPanel
  const editor = el("div", "studio-editor");
  const pipelineSlot = el("div", "studio-pipeline-slot");
  const noteSlot = el("div", "studio-note-slot");
  const editionOpsSlot = el("div", "studio-edition-ops-slot hidden");
  mountImportBanner(editor);
  mountImportPipelinePanel(pipelineSlot);
  mountNoteEditor(noteSlot);
  mountEditionOpsPanel(editionOpsSlot);
  editor.appendChild(pipelineSlot);
  editor.appendChild(noteSlot);
  editor.appendChild(editionOpsSlot);
  main.appendChild(editor);

  const graph = el("div", "studio-graph hidden");
  mountGraphPanel(graph);
  main.appendChild(graph);
  // 窄屏（≤1080px）时属性栏迁移到此底部停靠栏（由 LayoutShell 负责 DOM 搬迁）
  const bottomDock = el("section", "studio-bottom-dock hidden");
  main.appendChild(bottomDock);
  // AI 对话挂在中栏底部：上=预览，下=对话框（窄屏绝不挤开右栏）
  try {
    mountAiTerminal(main);
  } catch (e) {
    console.error("[studio] mountAiTerminal failed", e);
  }

  layout.appendChild(main);

  const rightDock = el("aside", "studio-right-dock");
  const rightResize = mountResizeHandle({
    panel: rightDock,
    edge: "left",
    storageKey: "studio-inspector-w",
    cssVar: "--inspector-w",
    minWidth: 220,
    maxWidth: 1100,
    maxWidthRatio: 0.62,
  });
  const rightBody = el("div", "studio-right-dock-body");
  const inspector = el("div", "studio-inspector");
  mountInspector(inspector);
  const editionInspector = el("div", "studio-edition-inspector hidden");
  mountEditionOpsInspector(editionInspector);
  const rightEdge = el("div", "studio-panel-edge studio-panel-edge--right");
  rightEdge.appendChild(rightResize.el);
  rightBody.appendChild(inspector);
  rightBody.appendChild(editionInspector);
  rightDock.appendChild(rightEdge);
  rightDock.appendChild(rightBody);
  layout.appendChild(rightDock);

  const syncCenter = (): void => {
    const edition = store.activeRibbon === "edition-ops";
    editionOpsSlot.classList.toggle("hidden", !edition);
    inspector.classList.toggle("hidden", edition);
    editionInspector.classList.toggle("hidden", !edition);
    rightDock.classList.toggle("studio-right-dock--edition", edition);
    subtitle.textContent = edition
      ? "配置导入用的解析方案（分栏 / 校对规则）"
      : "导入 PDF → 校对定稿 → 发布进检索库";
    if (edition) {
      pipelineSlot.classList.add("hidden");
      noteSlot.classList.add("hidden");
    } else if (!store.isImportWorkspace()) {
      noteSlot.classList.remove("hidden");
    }
  };
  store.subscribe(syncCenter);
  syncCenter();

  root.appendChild(layout);

  initLayoutShell({
    layout,
    leftSidebar: sidebar,
    sidePanelWrap,
    rightDock,
    rightBody,
    bottomDock,
  });

  mountCommandPalette();
  mountImportWizard();
  mountIndexHealth();
  mountMinerUSettings();
}
