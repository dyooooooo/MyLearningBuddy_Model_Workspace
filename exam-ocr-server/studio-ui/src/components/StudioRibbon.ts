import { store, type RibbonSection } from "../state/store";
import { el } from "../util/dom";

const ITEMS: { id: RibbonSection; icon: string; label: string }[] = [
  { id: "textbook", icon: "📚", label: "教材" },
  { id: "import", icon: "📥", label: "导入" },
  { id: "edition-ops", icon: "🧩", label: "方案" },
  { id: "improvements", icon: "✨", label: "改进点" },
  { id: "search", icon: "🔍", label: "搜索" },
  { id: "todo", icon: "📋", label: "待办" },
];

export function mountStudioRibbon(rootEl: HTMLElement): void {
  const ribbon = el("nav", "studio-ribbon");
  ribbon.setAttribute("aria-label", "语料库导航");
  rootEl.appendChild(ribbon);

  const buttons = new Map<RibbonSection, HTMLButtonElement>();

  for (const item of ITEMS) {
    const btn = el("button", "ribbon-btn") as HTMLButtonElement;
    btn.type = "button";
    btn.title = item.label;
    btn.setAttribute("aria-label", item.label);
    btn.dataset.section = item.id;
    const icon = el("span", "ribbon-btn-icon", item.icon);
    const label = el("span", "ribbon-btn-label", item.label);
    btn.append(icon, label);
    btn.onclick = () => {
      if (item.id === "import") {
        // 有活跃导入任务 → 恢复三栏；否则进任务列表首页
        void (async () => {
          try {
            const { loadActiveMinerUJob } = await import("./import/activeMinerUJob");
            const jid = loadActiveMinerUJob();
            if (jid) {
              store.focusImportWorkspace();
              store.showImportPreview();
              document.dispatchEvent(new CustomEvent("studio:import-restore-active"));
              return;
            }
          } catch {
            /* fall through */
          }
          store.showImportHub();
        })();
      } else {
        store.setActiveRibbon(item.id);
      }
    };
    buttons.set(item.id, btn);
    ribbon.appendChild(btn);
  }

  const syncActive = (): void => {
    const active = store.activeRibbon;
    buttons.forEach((btn, id) => {
      btn.classList.toggle("active", id === active);
      btn.setAttribute("aria-current", id === active ? "page" : "false");
    });
  };

  syncActive();
  store.subscribe(syncActive);
}
