const NARROW_BP = 1080;
const LEFT_COLLAPSED_KEY = "studio-left-panel-collapsed";
const RIGHT_COLLAPSED_KEY = "studio-right-panel-collapsed";
const BOTTOM_COLLAPSED_KEY = "studio-bottom-dock-collapsed";

export interface LayoutShellOptions {
  layout: HTMLElement;
  leftSidebar: HTMLElement;
  sidePanelWrap: HTMLElement;
  rightDock: HTMLElement;
  rightBody: HTMLElement;
  bottomDock: HTMLElement;
}

function loadCollapsed(key: string): boolean {
  try {
    return localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function saveCollapsed(key: string, collapsed: boolean): void {
  try {
    localStorage.setItem(key, collapsed ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function createDockToggle(side: "left" | "right", collapsed: boolean): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = `studio-dock-toggle studio-dock-toggle--${side}`;
  btn.setAttribute("aria-label", side === "left" ? "折叠左侧栏" : "折叠右侧栏");
  btn.dataset.collapsed = collapsed ? "1" : "0";
  return btn;
}

export function initLayoutShell(opts: LayoutShellOptions): void {
  const { layout, leftSidebar, sidePanelWrap, rightDock, rightBody, bottomDock } = opts;

  let leftCollapsed = loadCollapsed(LEFT_COLLAPSED_KEY);
  let rightCollapsed = loadCollapsed(RIGHT_COLLAPSED_KEY);
  let bottomCollapsed = loadCollapsed(BOTTOM_COLLAPSED_KEY);
  let narrowOverride = { left: false, right: false };

  const leftToggle = createDockToggle("left", leftCollapsed);
  const rightToggle = createDockToggle("right", rightCollapsed);

  const leftEdge = sidePanelWrap.querySelector(".studio-panel-edge--left");
  const rightEdge = rightDock.querySelector(".studio-panel-edge--right");
  if (leftEdge?.firstChild) {
    leftEdge.insertBefore(leftToggle, leftEdge.firstChild);
  } else {
    leftEdge?.appendChild(leftToggle);
  }
  rightEdge?.appendChild(rightToggle);

  const leftExpandRail = document.createElement("button");
  leftExpandRail.type = "button";
  leftExpandRail.className = "studio-dock-rail studio-dock-rail--left";
  leftExpandRail.setAttribute("aria-label", "展开左侧栏");
  leftExpandRail.title = "展开左侧栏";

  const rightExpandRail = document.createElement("button");
  rightExpandRail.type = "button";
  rightExpandRail.className = "studio-dock-rail studio-dock-rail--right";
  rightExpandRail.setAttribute("aria-label", "展开右侧栏");
  rightExpandRail.title = "展开右侧栏";

  layout.append(leftExpandRail, rightExpandRail);

  // 窄屏底部属性栏的标题栏 + 折叠按钮
  const bottomBar = document.createElement("div");
  bottomBar.className = "studio-bottom-dock__bar";
  const bottomTitle = document.createElement("span");
  bottomTitle.className = "studio-bottom-dock__title";
  bottomTitle.textContent = "属性 / AI 对话";
  const bottomToggle = document.createElement("button");
  bottomToggle.type = "button";
  bottomToggle.className = "studio-bottom-dock__toggle";
  bottomToggle.setAttribute("aria-label", "折叠或展开底部属性栏");
  bottomBar.append(bottomTitle, bottomToggle);
  bottomDock.appendChild(bottomBar);

  function isNarrow(): boolean {
    return window.matchMedia(`(max-width: ${NARROW_BP}px)`).matches;
  }

  function syncToggleLabels(): void {
    leftToggle.dataset.collapsed = leftCollapsed ? "1" : "0";
    rightToggle.dataset.collapsed = rightCollapsed ? "1" : "0";
    leftSidebar.classList.toggle("studio-dock--collapsed", leftCollapsed);
    rightDock.classList.toggle("studio-dock--collapsed", rightCollapsed);
  }

  function syncBottom(): void {
    bottomToggle.textContent = bottomCollapsed ? "▴ 展开" : "▾ 折叠";
    bottomDock.classList.toggle("studio-bottom-dock--collapsed", bottomCollapsed);
  }

  /** 宽屏：属性栏在右栏；窄屏：属性栏整体搬迁到中栏底部。 */
  function relocateRightBody(toBottom: boolean): void {
    if (toBottom) {
      if (rightBody.parentElement !== bottomDock) bottomDock.appendChild(rightBody);
      bottomDock.classList.remove("hidden");
      syncBottom();
    } else {
      if (rightBody.parentElement !== rightDock) rightDock.appendChild(rightBody);
      bottomDock.classList.add("hidden");
    }
  }

  function applyLayout(): void {
    const narrow = isNarrow();
    layout.classList.toggle("studio-layout--narrow", narrow);

    if (narrow) {
      leftCollapsed = !narrowOverride.left;
      // 窄屏不再保留右栏：属性栏迁到底部展示
      rightCollapsed = true;
      relocateRightBody(true);
    } else {
      narrowOverride = { left: false, right: false };
      leftCollapsed = loadCollapsed(LEFT_COLLAPSED_KEY);
      rightCollapsed = loadCollapsed(RIGHT_COLLAPSED_KEY);
      relocateRightBody(false);
    }

    syncToggleLabels();
  }

  function setLeftCollapsed(next: boolean, persist = true): void {
    leftCollapsed = next;
    if (isNarrow()) {
      narrowOverride.left = !next;
    } else if (persist) {
      saveCollapsed(LEFT_COLLAPSED_KEY, next);
    }
    syncToggleLabels();
  }

  function setRightCollapsed(next: boolean, persist = true): void {
    rightCollapsed = next;
    if (isNarrow()) {
      narrowOverride.right = !next;
    } else if (persist) {
      saveCollapsed(RIGHT_COLLAPSED_KEY, next);
    }
    syncToggleLabels();
  }

  leftToggle.addEventListener("click", () => setLeftCollapsed(!leftCollapsed));
  rightToggle.addEventListener("click", () => setRightCollapsed(!rightCollapsed));
  leftExpandRail.addEventListener("click", () => setLeftCollapsed(false));
  rightExpandRail.addEventListener("click", () => setRightCollapsed(false));

  function setBottomCollapsed(next: boolean): void {
    bottomCollapsed = next;
    saveCollapsed(BOTTOM_COLLAPSED_KEY, next);
    syncBottom();
  }

  bottomToggle.addEventListener("click", () => setBottomCollapsed(!bottomCollapsed));

  // AI 终端 / 页码条等：需要保证右栏展开
  document.addEventListener("studio:expand-right-dock", () => {
    if (isNarrow()) {
      setBottomCollapsed(false);
      return;
    }
    setRightCollapsed(false);
  });

  // 窄屏下 Ctrl+` 等诉求：展开底部属性栏（AI 在其中的页签里）
  document.addEventListener("studio:expand-bottom-dock", () => {
    setBottomCollapsed(false);
  });

  window.addEventListener("resize", applyLayout);
  applyLayout();
}
