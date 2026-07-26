/**
 * Ctrl+` AI 对话抽屉：挂在中栏（main）底部，与预览上下分栏。
 * 宽屏：抽屉式上下分栏；窄屏（≤1080px）：AI 已在底部属性栏的「AI 对话」页签里，
 * Ctrl+` 只负责展开底部栏并切到该页签，不再开抽屉。
 */

import { el } from "../util/dom";

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

/** 挂到中栏 main 底部；Ctrl+` / Cmd+` 开关。 */
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
    "尚无进行中的 AI 会话。在导入区选择方案与 PDF 后点「AI 分段导入」即可在此对话。",
  );
  slot.appendChild(placeholder);

  root.append(handle, head, slot);
  main.appendChild(root);

  let open = false;
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
        if (!open) return;
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

  function focusComposer(): void {
    window.setTimeout(() => {
      const ta = root.querySelector<HTMLTextAreaElement>(".copilot-composer-input");
      ta?.focus();
    }, 40);
  }

  function setOpen(next: boolean): void {
    if (next && window.matchMedia("(max-width: 1080px)").matches) {
      // 窄屏：属性栏（含 AI 页签）已在中栏底部，聚焦过去即可，不开抽屉
      document.dispatchEvent(new CustomEvent("studio:expand-bottom-dock"));
      void import("./Inspector").then(({ showImportCopilotTab }) => {
        showImportCopilotTab();
        focusComposer();
      });
      return;
    }
    open = next;
    root.classList.toggle("is-open", open);
    main.classList.toggle("studio-main--ai-terminal", open);
    document.documentElement.classList.toggle("studio-ai-terminal-open", open);
    if (open) {
      // 窄屏单栏：绝不展开右栏，避免把中栏挤没
      applyHeight(height);
      parkCopilotHost();
      focusComposer();
    } else {
      restoreCopilotHost();
    }
  }

  closeBtn.onclick = () => setOpen(false);

  handle.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || !open) return;
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
    if (open) applyHeight(height);
  });

  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    if (e.code !== "Backquote" && e.key !== "`") return;
    e.preventDefault();
    setOpen(!open);
  });

  document.addEventListener("studio:toggle-ai-terminal", (() => {
    setOpen(!open);
  }) as EventListener);

  document.addEventListener("studio:open-ai-terminal", (() => {
    setOpen(true);
  }) as EventListener);

  document.addEventListener("studio:inspector-tab", ((e: CustomEvent<{ tab?: string }>) => {
    if (open && e.detail?.tab === "ai") parkCopilotHost();
  }) as EventListener);

  applyHeight(height);

  return {
    el: root,
    isOpen: () => open,
    open: () => setOpen(true),
    close: () => setOpen(false),
    toggle: () => setOpen(!open),
  };
}
