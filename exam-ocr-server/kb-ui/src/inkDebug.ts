const KEY = "kb-ir-ink-debug";
const PLAIN = "is-ink-plain";

/** 默认开着：跟现在一样，每块有虚框和底色。 */
export function inkDebugOn(): boolean {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw == null) return true;
    return raw === "1";
  } catch {
    return true;
  }
}

export function setInkDebug(on: boolean): void {
  try {
    localStorage.setItem(KEY, on ? "1" : "0");
  } catch {
    /* 无存储时仍改这一屏 */
  }
}

/** 关掉块框时给影子宿主加 is-ink-plain，theme.css 据此去掉虚框和底色。 */
export function stampInkDebug(host: HTMLElement): void {
  host.classList.toggle(PLAIN, !inkDebugOn());
}

export function applyInkDebug(root: ParentNode = document): void {
  const on = inkDebugOn();
  for (const host of root.querySelectorAll<HTMLElement>(".kb-page-body")) {
    if (host.shadowRoot?.querySelector(".kb-ir-page")) stampInkDebug(host);
  }
  for (const btn of root.querySelectorAll<HTMLButtonElement>(".kb-ink-debug")) {
    syncInkDebugButton(btn, on);
  }
}

export function syncInkDebugButton(btn: HTMLButtonElement, on = inkDebugOn()): void {
  btn.classList.toggle("is-active", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.title = on ? "关掉各块的虚框和底色" : "显示各块的虚框和底色";
}
