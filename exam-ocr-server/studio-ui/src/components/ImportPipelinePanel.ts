// Import pipeline dispatcher — 中间区四态互斥：首页 / 预览 / 新建 / MinerU 设置
// Panels load on demand (current line only) to keep the main bundle light.

import { store } from "../state/store";
import { el } from "../util/dom";
import { B35_IMPORT_LINE } from "./import/B35ImportTree";
import { mountMinerUSettingsPanel } from "../views/MinerUSettings";
import { mountImportProjectList } from "./import/ImportProjectList";
import type { PanelHandle } from "./import/types";

type Line = "mineru" | "b35";
const LINE_KEY = "studio-import-line";

function loadLine(): Line {
  try {
    const v = localStorage.getItem(LINE_KEY);
    if (v === "mineru" || v === "b35") return v;
  } catch {
    /* ignore */
  }
  return "mineru";
}

export function mountImportPipelinePanel(container: HTMLElement): { destroy: () => void } {
  let line: Line = loadLine();
  let mineru: PanelHandle | null = null;
  let b35: PanelHandle | null = null;
  const disposers: Array<() => void> = [];

  const root = el("div", "import-pipeline-panel");

  const hubShell = el("div", "import-center-hub");
  mountImportProjectList(hubShell);
  root.appendChild(hubShell);

  const setupShell = el("div", "import-center-setup");
  const tabs = el("div", "import-line-tabs");
  const tabMineru = el("button", "import-line-tab", "日常教材（MinerU）") as HTMLButtonElement;
  const tab35b = el("button", "import-line-tab", "难版式对照（35B）") as HTMLButtonElement;
  tabMineru.type = "button";
  tab35b.type = "button";
  tabs.append(tabMineru, tab35b);
  const lineHint = el(
    "p",
    "muted import-line-hint",
    "不确定选哪条？日常教材用左侧；分栏乱/公式多再试右侧。导入完成后点「在教材中打开」去定稿。",
  );
  const setupPanels = el("div", "import-setup-panels");
  setupShell.append(tabs, lineHint, setupPanels);
  root.appendChild(setupShell);

  const previewShell = el("div", "import-center-preview");
  const panels = el("div", "import-line-panels");
  previewShell.appendChild(panels);
  root.appendChild(previewShell);

  const settingsShell = el("div", "import-center-mineru-settings");
  mountMinerUSettingsPanel(settingsShell);
  root.appendChild(settingsShell);

  container.appendChild(root);

  let lastAppliedView: typeof store.importCenterView | "" = "";
  // 并发防护：多个事件同时触发 applyLine 时，`await import()` 间隙会让
  // `if (!mineru)` 检查被穿透 → 面板被重复挂载成多个独立滚动窗格。
  // 缓存 in-flight Promise，确保每条线只挂载一次。
  let mineruPromise: Promise<PanelHandle> | null = null;
  let b35Promise: Promise<PanelHandle> | null = null;

  async function ensureLineMounted(which: Line): Promise<PanelHandle> {
    if (which === "mineru") {
      if (!mineruPromise) {
        mineruPromise = (async () => {
          const mod = await import("./import/MinerUPipelinePanel");
          mineru = mod.mountMinerUPipelinePanel();
          setupPanels.appendChild(mineru.setupRoot);
          panels.appendChild(mineru.previewRoot);
          return mineru;
        })();
      }
      return mineruPromise;
    }
    if (!b35Promise) {
      b35Promise = (async () => {
        const mod = await import("./import/B35PipelinePanel");
        b35 = mod.mountB35PipelinePanel();
        setupPanels.appendChild(b35.setupRoot);
        panels.appendChild(b35.previewRoot);
        return b35;
      })();
    }
    return b35Promise;
  }

  async function applyLine(refreshPanels = false): Promise<void> {
    const isMineru = line === "mineru";
    tabMineru.classList.toggle("active", isMineru);
    tab35b.classList.toggle("active", !isMineru);
    const active = await ensureLineMounted(isMineru ? "mineru" : "b35");
    if (mineru) {
      mineru.setupRoot.classList.toggle("hidden", !isMineru);
      mineru.previewRoot.classList.toggle("hidden", !isMineru);
    }
    if (b35) {
      b35.setupRoot.classList.toggle("hidden", isMineru);
      b35.previewRoot.classList.toggle("hidden", isMineru);
    }
    if (refreshPanels) active.refresh();
  }

  function setLine(next: Line): void {
    if (line === next) return;
    line = next;
    try {
      localStorage.setItem(LINE_KEY, next);
    } catch {
      /* ignore */
    }
    void applyLine(true);
  }

  tabMineru.onclick = () => setLine("mineru");
  tab35b.onclick = () => setLine("b35");
  disposers.push(
    (() => {
      const h = () => setLine("b35");
      document.addEventListener(B35_IMPORT_LINE, h);
      return () => document.removeEventListener(B35_IMPORT_LINE, h);
    })(),
  );
  disposers.push(
    (() => {
      const h = () => setLine("mineru");
      document.addEventListener("studio:import-line-mineru", h);
      return () => document.removeEventListener("studio:import-line-mineru", h);
    })(),
  );

  function syncEditorLayout(): void {
    const isImport = store.isImportWorkspace();
    document.querySelector(".studio-editor")?.classList.toggle("studio-editor--import", isImport);
    document.querySelector(".studio-app")?.classList.toggle("studio-app--import", isImport);
  }

  function syncCenterView(): void {
    const view = store.importCenterView;
    hubShell.classList.toggle("hidden", view !== "hub");
    setupShell.classList.toggle("hidden", view !== "setup");
    previewShell.classList.toggle("hidden", view !== "preview");
    settingsShell.classList.toggle("hidden", view !== "mineru-settings");
    const viewChanged = view !== lastAppliedView;
    lastAppliedView = view;
    if (view === "preview" || view === "setup") void applyLine(viewChanged);
  }

  function syncVisibility(): void {
    const show = store.isImportWorkspace();
    root.classList.toggle("hidden", !show);
    syncEditorLayout();
    syncCenterView();
  }

  disposers.push(
    store.subscribeSelector(
      (s) => `${s.activeRibbon}|${s.importMode}|${s.importCenterView}`,
      () => {
        syncCenterView();
        syncVisibility();
      },
    ),
  );

  for (const name of [
    "studio:import-workspace-focus",
    "studio:import-new",
    "studio:import-hub",
  ] as const) {
    const h = () => {
      syncCenterView();
      syncVisibility();
    };
    document.addEventListener(name, h);
    disposers.push(() => document.removeEventListener(name, h));
  }

  void applyLine();
  syncVisibility();

  async function restoreActiveTriPane(): Promise<void> {
    try {
      const { loadActiveMinerUJob, focusMinerUJobTriPane } = await import("./import/activeMinerUJob");
      const jid = loadActiveMinerUJob();
      if (!jid) return;
      if (!store.isImportWorkspace()) return;
      setLine("mineru");
      store.showImportPreview();
      focusMinerUJobTriPane(jid);
      const panel = await ensureLineMounted("mineru");
      if (panel.restoreSessionForJob) await panel.restoreSessionForJob(jid);
    } catch (e) {
      console.warn("恢复导入三栏失败", e);
    }
  }

  disposers.push(
    (() => {
      const h = () => void restoreActiveTriPane();
      document.addEventListener("studio:import-restore-active", h);
      return () => document.removeEventListener("studio:import-restore-active", h);
    })(),
  );

  if (store.isImportWorkspace()) {
    void restoreActiveTriPane();
  }

  return {
    destroy: () => {
      for (const d of disposers) d();
    },
  };
}
