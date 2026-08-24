const NARROW_BP = 1080;
const LEFT_COLLAPSED_KEY = "studio-left-panel-collapsed";
const RIGHT_COLLAPSED_KEY = "studio-right-panel-collapsed";
const BOTTOM_COLLAPSED_KEY = "studio-bottom-dock-collapsed";
/** 属性栏停靠：right=右栏 · bottom=中栏下栏 */
const INSPECTOR_DOCK_KEY = "studio-inspector-dock";

export type InspectorDockPlacement = "right" | "bottom";

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

function loadDockPlacement(): InspectorDockPlacement | null {
  try {
    const v = localStorage.getItem(INSPECTOR_DOCK_KEY);
    if (v === "right" || v === "bottom") return v;
  } catch {
    /* ignore */
  }
  return null;
}

function saveDockPlacement(placement: InspectorDockPlacement): void {
  try {
    localStorage.setItem(INSPECTOR_DOCK_KEY, placement);
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

/** 当前属性栏是否停在下栏（供 AI 打开逻辑读取）。 */
export function isInspectorDockBottom(): boolean {
  return document.documentElement.classList.contains("studio-inspector-dock-bottom");
}

export function initLayoutShell(opts: LayoutShellOptions): void {
  const { layout, leftSidebar, sidePanelWrap, rightDock, rightBody, bottomDock } = opts;

  let leftCollapsed = loadCollapsed(LEFT_COLLAPSED_KEY);
  let rightCollapsed = loadCollapsed(RIGHT_COLLAPSED_KEY);
  let bottomCollapsed = loadCollapsed(BOTTOM_COLLAPSED_KEY);
  let narrowOverride = { left: false, right: false };

  // 用户手动停靠优先；未设置时随页宽默认（窄→下栏，宽→右栏）
  let dockPlacement: InspectorDockPlacement =
    loadDockPlacement() ??
    (window.matchMedia(`(max-width: ${NARROW_BP}px)`).matches ? "bottom" : "right");

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

  // 窄屏 / 下栏模式：底部属性栏的标题栏 + 折叠按钮
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

  function emitDockPlacement(): void {
    document.documentElement.classList.toggle(
      "studio-inspector-dock-bottom",
      dockPlacement === "bottom",
    );
    layout.dataset.inspectorDock = dockPlacement;
    document.dispatchEvent(
      new CustomEvent("studio:inspector-dock", { detail: { placement: dockPlacement } }),
    );
  }

  /** 把属性栏 DOM 放到右栏或中栏下栏。 */
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

  function applyDockPlacement(): void {
    const toBottom = dockPlacement === "bottom";
    relocateRightBody(toBottom);

    if (toBottom) {
      // 下栏模式：收起右栏占位，主区拉宽
      rightCollapsed = true;
      bottomCollapsed = false;
      syncBottom();
    } else {
      // 右栏模式：展开右栏，隐藏下栏
      if (isNarrow()) {
        narrowOverride.right = true;
        rightCollapsed = false;
      } else {
        rightCollapsed = loadCollapsed(RIGHT_COLLAPSED_KEY);
      }
    }
    syncToggleLabels();
    emitDockPlacement();
  }

  function setDockPlacement(next: InspectorDockPlacement, persist = true): void {
    dockPlacement = next;
    if (persist) saveDockPlacement(next);
    applyDockPlacement();
  }

  function toggleDockPlacement(): void {
    setDockPlacement(dockPlacement === "bottom" ? "right" : "bottom");
  }

  function applyLayout(): void {
    const narrow = isNarrow();
    layout.classList.toggle("studio-layout--narrow", narrow);

    if (narrow) {
      leftCollapsed = !narrowOverride.left;
    } else {
      narrowOverride = { left: false, right: false };
      leftCollapsed = loadCollapsed(LEFT_COLLAPSED_KEY);
    }

    // 未手动选过停靠时，随页宽给默认；已有偏好则 resize 不抢
    if (loadDockPlacement() == null) {
      dockPlacement = narrow ? "bottom" : "right";
    }

    applyDockPlacement();
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
    // 下栏模式下右栏本来就是收起的；若用户点展开右栏，先切回右栏停靠
    if (!next && dockPlacement === "bottom") {
      setDockPlacement("right");
      return;
    }
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

  /** 展开当前属性栏所在停靠位（不改变右/下位置）。 */
  function expandInspectorDock(): void {
    if (dockPlacement === "bottom") setBottomCollapsed(false);
    else setRightCollapsed(false);
  }

  document.addEventListener("studio:expand-right-dock", () => {
    expandInspectorDock();
  });

  document.addEventListener("studio:expand-bottom-dock", () => {
    expandInspectorDock();
  });

  document.addEventListener("studio:toggle-inspector-dock", () => {
    toggleDockPlacement();
  });

  window.addEventListener("resize", applyLayout);
  applyLayout();
}
