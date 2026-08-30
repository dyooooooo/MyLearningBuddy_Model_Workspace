import { el } from "./util/dom";

export type KbTabId = "books" | "import" | "agent" | "agent-config" | "tasks";

const ITEMS: { id: KbTabId; icon: string; label: string; sub?: boolean }[] = [
  { id: "import", icon: "📥", label: "导入" },
  { id: "books", icon: "📚", label: "知识库" },
  { id: "tasks", icon: "☑", label: "任务" },
  { id: "agent", icon: "✦", label: "轨迹" },
  { id: "agent-config", icon: "⚙", label: "配置", sub: true },
];

export function mountKbRail(
  host: HTMLElement,
  opts: { initial?: KbTabId; onChange: (id: KbTabId) => void },
): { setActive: (id: KbTabId) => void; getActive: () => KbTabId } {
  let active: KbTabId = opts.initial || "books";
  const nav = el("nav", "kb-rail");
  nav.setAttribute("aria-label", "知识库导航");
  const buttons = new Map<KbTabId, HTMLButtonElement>();

  function paint(): void {
    buttons.forEach((btn, id) => {
      btn.classList.toggle("is-active", id === active);
      btn.setAttribute("aria-current", id === active ? "page" : "false");
    });
  }

  for (const item of ITEMS) {
    const btn = el("button", item.sub ? "kb-rail-btn kb-rail-btn--sub" : "kb-rail-btn") as HTMLButtonElement;
    btn.type = "button";
    btn.title = item.label;
    btn.setAttribute("aria-label", item.label);
    btn.append(el("span", "kb-rail-icon", item.icon), el("span", "kb-rail-label", item.label));
    btn.onclick = () => {
      if (active === item.id) return;
      active = item.id;
      paint();
      opts.onChange(item.id);
    };
    buttons.set(item.id, btn);
    nav.appendChild(btn);
  }

  host.appendChild(nav);
  paint();
  return {
    setActive: (id) => {
      if (active === id) return;
      active = id;
      paint();
      opts.onChange(id);
    },
    getActive: () => active,
  };
}
