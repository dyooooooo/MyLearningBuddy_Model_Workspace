/**
 * AI 对话显隐协调：
 * - 跟随属性栏当前停靠（右栏 / 下栏），展开对应区域并切到「AI 对话」页签
 * - 不再按页宽硬开中栏遗留抽屉
 *
 * Ctrl+` / studio:open-ai-terminal / 双击聚焦 共用同一套逻辑。
 */

import { el } from "../util/dom";
import { isInspectorDockBottom } from "./LayoutShell";
const HEIGHT_KEY = "studio.ai-terminal-h";
const DEFAULT_H = 360;
const MIN_H = 180;
const MAX_H_RATIO = 0.72;

export type AiTerminalHandle = {
  el: HTMLElement;
  isOpen: () => boolean;
  open: () => void;
  close: () => void;
  toggle: () => void;
};

function loadHeight(): number {
  try {
    const n = Number(localStorage.getItem(HEIGHT_KEY) || "");
    if (Number.isFinite(n) && n >= MIN_H) return Math.round(n);
  } catch {
    /* ignore */
  }
  return DEFAULT_H;
}

function saveHeight(h: number): void {
  try {
    localStorage.setItem(HEIGHT_KEY, String(Math.round(h)));
  } catch {
    /* ignore */
  }
}

function maxHeight(): number {
  const main = document.querySelector(".studio-main") as HTMLElement | null;
  const base = main?.clientHeight || window.innerHeight;
  return Math.max(MIN_H + 40, Math.floor(base * MAX_H_RATIO));
}

function clampHeight(h: number): number {
  return Math.min(maxHeight(), Math.max(MIN_H, Math.round(h)));
}

/** 挂到中栏 main 底部；Ctrl+` / Cmd+` 按布局适配打开 AI 宿主。 */
export function mountAiTerminal(main: HTMLElement): AiTerminalHandle {
  const root = el("div", "studio-ai-terminal");
  root.setAttribute("role", "complementary");
  root.setAttribute("aria-label", "AI 对话");

  const handle = el("div", "studio-ai-terminal__resize");
  handle.title = "拖动调整高度";
  handle.setAttribute("aria-label", "调整 AI 对话高度");

  const head = el("div", "studio-ai-terminal__head");
  const title = el("div", "studio-ai-terminal__title");
  title.append(
    el("span", "studio-ai-terminal__title-mark", "AI"),
    el("span", "studio-ai-terminal__title-text", "对话"),
    el("span", "studio-ai-terminal__kbd", "Ctrl+`"),
  );
  const closeBtn = el("button", "studio-ai-terminal__close", "×") as HTMLButtonElement;
  closeBtn.type = "button";
  closeBtn.title = "关闭（Ctrl+`）";
  closeBtn.setAttribute("aria-label", "关闭 AI 对话");
  head.append(title, closeBtn);

  const slot = el("div", "studio-ai-terminal__slot");
  const placeholder = el(
    "div",
    "studio-ai-terminal__empty muted",
    "尚无进行中的 AI 会话。在导入区选择方案与 PDF 后点「AI 全书导入」即可在此对话。",
  );
  slot.appendChild(placeholder);

  root.append(handle, head, slot);
  main.appendChild(root);

  /** 遗留底部抽屉是否处于打开态（宽屏新逻辑下应保持 false）。 */
  let drawerOpen = false;
  let height = loadHeight();
  let homeParent: HTMLElement | null = null;
  let homeNext: ChildNode | null = null;
  let parkedHost: HTMLElement | null = null;

  function applyHeight(h: number): void {
    height = clampHeight(h);
    root.style.height = `${height}px`;
    saveHeight(height);
  }

  function parkCopilotHost(): void {
    try {
      void import("./Inspector").then(({ getImportCopilotHost }) => {
        if (!drawerOpen) return;
        const copilotHost = getImportCopilotHost();
        if (!copilotHost || parkedHost === copilotHost) return;
        if (slot.contains(copilotHost)) {
          parkedHost = copilotHost;
          return;
        }
        homeParent = copilotHost.parentElement;
        homeNext = copilotHost.nextSibling;
        parkedHost = copilotHost;
        placeholder.remove();
        slot.appendChild(copilotHost);
        focusComposer();
      });
    } catch (e) {
      console.warn("[studio] park AI host failed", e);
    }
  }

  function restoreCopilotHost(): void {
    try {
      if (!parkedHost) return;
      if (homeParent) {
        if (homeNext && homeNext.parentNode === homeParent) {
          homeParent.insertBefore(parkedHost, homeNext);
        } else {
          homeParent.appendChild(parkedHost);
        }
      }
    } catch (e) {
      console.warn("[studio] restore AI host failed", e);
    } finally {
      parkedHost = null;
      homeParent = null;
      homeNext = null;
      if (!slot.querySelector(".inspector-copilot-slot, .copilot-inline")) {
        slot.appendChild(placeholder);
      }
    }
  }

  function setDrawerOpen(next: boolean): void {
    drawerOpen = next;
    root.classList.toggle("is-open", drawerOpen);
    main.classList.toggle("studio-main--ai-terminal", drawerOpen);
    document.documentElement.classList.toggle("studio-ai-terminal-open", drawerOpen);
    if (drawerOpen) {
      applyHeight(height);
      parkCopilotHost();
    } else {
      restoreCopilotHost();
    }
  }

  /** 关闭遗留中栏底部抽屉，把 Copilot 宿主还回属性栏。 */
  function closeLegacyDrawer(): void {
    if (drawerOpen) setDrawerOpen(false);
    else restoreCopilotHost();
  }

  function focusComposer(): void {
    window.setTimeout(() => {
      void import("./Inspector").then(({ getImportCopilotHost }) => {
        const host = getImportCopilotHost();
        const ta =
          host?.querySelector<HTMLTextAreaElement>(".copilot-composer-input") ||
          root.querySelector<HTMLTextAreaElement>(".copilot-composer-input");
        ta?.focus();
      });
    }, 40);
  }

  /** 跟随属性栏停靠位打开 AI（右栏或下栏），不挪动停靠、不开遗留抽屉。 */
  function revealAiSurface(): void {
    closeLegacyDrawer();
    if (isInspectorDockBottom()) {
      document.dispatchEvent(new CustomEvent("studio:expand-bottom-dock"));
    } else {
      document.dispatchEvent(new CustomEvent("studio:expand-right-dock"));
    }
    void import("./Inspector").then(({ showImportCopilotTab }) => {
      showImportCopilotTab();
      focusComposer();
    });
  }

  function setOpen(next: boolean): void {
    if (next) {
      revealAiSurface();
      return;
    }
    // 关闭：收起遗留抽屉；窄屏额外折叠底部栏（可选，保持轻量只关抽屉）
    closeLegacyDrawer();
  }

  closeBtn.onclick = () => setOpen(false);

  handle.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || !drawerOpen) return;
    ev.preventDefault();
    const startY = ev.clientY;
    const startH = height;
    handle.setPointerCapture(ev.pointerId);
    document.body.classList.add("studio-resizing-row");

    const onMove = (e: PointerEvent) => {
      applyHeight(startH + (startY - e.clientY));
    };
    const onUp = () => {
      handle.releasePointerCapture(ev.pointerId);
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      document.body.classList.remove("studio-resizing-row");
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
  });

  window.addEventListener("resize", () => {
    if (drawerOpen) closeLegacyDrawer();
  });

  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.code !== "Backquote" && e.key !== "`") return;
    e.preventDefault();
    if (drawerOpen) setOpen(false);
    else revealAiSurface();
  });

  document.addEventListener("studio:toggle-ai-terminal", (() => {
    if (drawerOpen) setOpen(false);
    else revealAiSurface();
  }) as EventListener);

  document.addEventListener("studio:open-ai-terminal", (() => {
    revealAiSurface();
  }) as EventListener);

  document.addEventListener(
    "studio:focus-import-target",
    ((e: CustomEvent<{
      line: "mineru" | "35b";
      jobId: string;
      pageNo: number;
      blockId?: string;
    }>) => {
      revealAiSurface();
      const detail = e.detail;
      if (!detail?.jobId || !detail.pageNo) return;
      void import("./CopilotPanel").then(({ focusImportTarget }) => {
        void focusImportTarget(detail);
      });
    }) as EventListener,
  );

  // 仅当遗留抽屉仍开着时，切到 AI 页签才把宿主 park 进抽屉（宽屏新路径不会触发）
  document.addEventListener("studio:inspector-tab", ((e: CustomEvent<{ tab?: string }>) => {
    if (drawerOpen && e.detail?.tab === "ai") parkCopilotHost();
  }) as EventListener);

  applyHeight(height);

  return {
    el: root,
    isOpen: () => drawerOpen,
    open: () => setOpen(true),
    close: () => setOpen(false),
    toggle: () => {
      if (drawerOpen) setOpen(false);
      else revealAiSurface();
    },
  };
}
