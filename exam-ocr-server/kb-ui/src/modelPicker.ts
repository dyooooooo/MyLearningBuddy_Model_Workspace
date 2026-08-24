import { closePopover } from "./popover";
import { el } from "./util/dom";

export type ModelParam = { id: string; value: string };
export type ModelValue = { value: string; display_name?: string; displayName?: string };
export type ModelParameter = {
  id: string;
  display_name?: string;
  displayName?: string;
  values: ModelValue[];
};
export type ModelVariant = {
  display_name?: string;
  displayName?: string;
  is_default?: boolean;
  isDefault?: boolean;
  params: ModelParam[];
};
export type CatalogModel = {
  id: string;
  label: string;
  group?: "auto" | "cursor" | "other" | string;
  parameters?: ModelParameter[];
  variants?: ModelVariant[];
};

const MODEL_KEY = "kb.dock.model";
const PARAMS_KEY = "kb.dock.model_params";
const ENABLED_KEY = "kb.dock.model_enabled";
const EXTRA_ENABLED = ["claude-opus-5", "gpt-5.6-sol"];
const OPTIMIZE_LABEL: Record<string, string> = {
  cost: "Cost",
  balanced: "Balance",
  intelligence: "Intelligence",
};

let open: { root: HTMLElement; onClose: () => void } | null = null;

export function readModelId(): string {
  return lsGet(MODEL_KEY);
}

export function saveModelState(modelId: string, params: ModelParam[]): void {
  persist(modelId, params);
}

export function readModelParams(): ModelParam[] {
  try {
    const raw = JSON.parse(lsGet(PARAMS_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((p) => p && p.id) : [];
  } catch {
    return [];
  }
}

export function statusLabel(model: CatalogModel | undefined, params: ModelParam[]): string {
  if (!model || isAuto(model.id)) {
    const opt = optimizeParam(model);
    if (opt) {
      const cur = paramValue(params, opt.id) || defaultParam(model, opt.id) || "balanced";
      return `Auto ${OPTIMIZE_LABEL[cur] || valueLabel(opt, cur) || "Balance"}`;
    }
    return "Auto";
  }
  const parts: string[] = [];
  const effort = effortParam(model);
  if (effort) {
    const cur = paramValue(params, effort.id) || defaultParam(model, effort.id);
    const name = valueLabel(effort, cur);
    if (name) parts.push(name);
  }
  if (hasParam(model, "fast") && paramValue(params, "fast") === "true") parts.push("Fast");
  return parts.join(" ") || model.label || model.id;
}

export function defaultParams(model: CatalogModel | undefined): ModelParam[] {
  if (!model) return [];
  const variant = (model.variants || []).find((v) => v.is_default || v.isDefault) || (model.variants || [])[0];
  return [...(variant?.params || [])];
}

export function openModelPicker(
  anchor: HTMLElement,
  catalog: CatalogModel[],
  state: { modelId: string; params: ModelParam[] },
  onChange: (next: { modelId: string; params: ModelParam[] }) => void,
): void {
  closePopover();
  if (open) {
    closeModelPicker();
    return;
  }
  const root = el("div", "kb-mp-root");
  document.body.append(root);

  let modelId = state.modelId;
  let params = [...state.params];
  let enabled = readEnabled(catalog, modelId);
  let showAll = false;
  let needle = "";
  let first = true;

  const commit = (nextId: string, nextParams: ModelParam[], keepOpen = true): void => {
    modelId = nextId;
    params = nextParams;
    persist(modelId, params);
    onChange({ modelId, params });
    if (keepOpen) paint();
    else closeModelPicker();
  };

  const paint = (): void => {
    const model = findModel(catalog, modelId);
    root.replaceChildren();
    const panel = el("div", "kb-mp-panel kb-mp-one");
    const opt = optimizeParam(model);
    if (opt) {
      panel.append(chipRow("Optimize For", opt, params, (value) => commit(modelId, setParam(params, opt.id, value))));
    } else {
      if (hasParam(model, "fast")) {
        const row = el("div", "kb-mp-row");
        row.append(el("span", "", "Fast"));
        const tog = el("button", `kb-toggle${paramValue(params, "fast") === "true" ? " is-on" : ""}`) as HTMLButtonElement;
        tog.type = "button";
        tog.setAttribute("role", "switch");
        tog.setAttribute("aria-checked", paramValue(params, "fast") === "true" ? "true" : "false");
        tog.append(el("span", "kb-toggle-knob"));
        tog.onclick = (ev) => {
          ev.stopPropagation();
          const next = paramValue(params, "fast") === "true" ? "false" : "true";
          commit(modelId, setParam(params, "fast", next));
        };
        row.append(tog);
        panel.append(row);
      }
      const effort = effortParam(model);
      if (effort) {
        panel.append(chipRow(effort.display_name || effort.displayName || "Effort", effort, params, (value) => {
          commit(modelId, setParam(params, effort.id, value));
        }));
      }
    }

    const q = document.createElement("input");
    q.className = "kb-mp-search";
    q.placeholder = "Search models";
    q.value = needle;
    q.oninput = () => {
      needle = q.value;
      paint();
      const again = root.querySelector<HTMLInputElement>(".kb-mp-search");
      if (again) {
        again.focus();
        again.setSelectionRange(needle.length, needle.length);
      }
    };
    panel.append(q);
    panel.append(modelList(catalog, enabled, showAll, needle, modelId, params, (next) => {
      if (!enabled.includes(next.id)) {
        enabled = [...enabled, next.id];
        writeEnabled(enabled);
      }
      commit(next.id, defaultParams(next), true);
    }));
    const hidden = catalog.filter((m) => !isAuto(m.id) && !enabled.includes(m.id)).length;
    if (hidden || showAll) {
      const add = el("button", "kb-mp-add") as HTMLButtonElement;
      add.type = "button";
      add.textContent = showAll ? "Show fewer" : "Add Models";
      add.onclick = () => {
        showAll = !showAll;
        paint();
      };
      panel.append(add);
    }
    root.append(panel);
    place(root, anchor);
    if (first) {
      first = false;
      window.setTimeout(() => q.focus(), 0);
    }
  };

  const onDoc = (ev: Event) => {
    const t = ev.target as Node | null;
    if (t && (root.contains(t) || anchor.contains(t))) return;
    closeModelPicker();
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key !== "Escape") return;
    closeModelPicker();
  };
  window.setTimeout(() => {
    document.addEventListener("pointerdown", onDoc, true);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", closeModelPicker);
    window.addEventListener("scroll", closeModelPicker, true);
  }, 0);
  open = {
    root,
    onClose: () => {
      document.removeEventListener("pointerdown", onDoc, true);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", closeModelPicker);
      window.removeEventListener("scroll", closeModelPicker, true);
    },
  };
  paint();
}

export function closeModelPicker(): void {
  if (!open) return;
  open.root.remove();
  open.onClose();
  open = null;
}

function chipRow(
  title: string,
  param: ModelParameter,
  params: ModelParam[],
  pick: (value: string) => void,
): HTMLElement {
  const wrap = el("div", "kb-mp-effort");
  wrap.append(el("span", "kb-mp-effort-h", title));
  const chips = el("div", "kb-mp-chips");
  const cur = paramValue(params, param.id) || param.values?.[0]?.value || "";
  for (const v of param.values || []) {
    const label = OPTIMIZE_LABEL[v.value] || valueLabel(param, v.value) || v.value;
    if (!label && v.value === "false") continue;
    const btn = el("button", `kb-mp-chip${v.value === cur ? " is-on" : ""}`) as HTMLButtonElement;
    btn.type = "button";
    btn.textContent = label || v.value;
    btn.onclick = () => pick(v.value);
    chips.append(btn);
  }
  wrap.append(chips);
  return wrap;
}

function modelList(
  catalog: CatalogModel[],
  enabled: string[],
  showAll: boolean,
  rawNeedle: string,
  modelId: string,
  params: ModelParam[],
  pick: (m: CatalogModel) => void,
): HTMLElement {
  const list = el("div", "kb-mp-list");
  const needle = rawNeedle.trim().toLowerCase();
  const shown = catalog.filter((m) => {
    if (!showAll && !enabled.includes(m.id) && !isAuto(m.id)) return false;
    if (!needle) return true;
    return m.id.toLowerCase().includes(needle) || (m.label || "").toLowerCase().includes(needle);
  });
  const auto = shown.filter((m) => isAuto(m.id));
  const cursor = shown.filter((m) => m.group === "cursor");
  const other = shown.filter((m) => !isAuto(m.id) && m.group !== "cursor");
  if (!shown.length) list.append(el("p", "muted kb-mp-empty", "No matching models"));
  for (const m of auto) list.append(modelItem(m, modelId, params, pick));
  if (cursor.length) {
    list.append(el("p", "kb-mp-h", "Cursor Models"));
    for (const m of cursor) list.append(modelItem(m, modelId, params, pick));
  }
  if (other.length) {
    list.append(el("p", "kb-mp-h", "Other Models"));
    for (const m of other) list.append(modelItem(m, modelId, params, pick));
  }
  return list;
}

function modelItem(
  model: CatalogModel,
  modelId: string,
  params: ModelParam[],
  pick: (m: CatalogModel) => void,
): HTMLElement {
  const on = model.id === modelId;
  const btn = el("button", `kb-mp-choice${on ? " is-on" : ""}`) as HTMLButtonElement;
  btn.type = "button";
  btn.append(el("span", "kb-mp-name", isAuto(model.id) ? "Auto" : model.label || model.id));
  const meta = on ? statusLabel(model, params) : defaultLabel(model);
  if (meta && !isAuto(model.id)) btn.append(el("span", "kb-mp-meta", meta));
  if (on) btn.append(checkSvg());
  btn.onclick = () => pick(model);
  return btn;
}

function place(root: HTMLElement, anchor: HTMLElement): void {
  const a = anchor.getBoundingClientRect();
  const gap = 6;
  const w = 300;
  let left = a.left;
  left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
  root.style.left = `${left}px`;
  const h = root.offsetHeight || 280;
  const spaceAbove = a.top - gap - 8;
  const spaceBelow = window.innerHeight - a.bottom - gap - 8;
  if (spaceAbove >= Math.min(h, 160) || spaceAbove > spaceBelow) {
    root.style.top = "auto";
    root.style.bottom = `${window.innerHeight - a.top + gap}px`;
  } else {
    root.style.bottom = "auto";
    root.style.top = `${a.bottom + gap}px`;
  }
}

function readEnabled(catalog: CatalogModel[], modelId: string): string[] {
  let saved: string[] = [];
  try {
    const raw = JSON.parse(lsGet(ENABLED_KEY) || "[]");
    if (Array.isArray(raw)) saved = raw.map(String);
  } catch {
    saved = [];
  }
  const seed = catalog
    .filter((m) => isAuto(m.id) || m.group === "cursor" || EXTRA_ENABLED.includes(m.id))
    .map((m) => m.id);
  const ids = new Set([...seed, ...saved, modelId].filter(Boolean));
  return [...ids];
}

function writeEnabled(ids: string[]): void {
  lsSet(ENABLED_KEY, JSON.stringify(ids));
}

function persist(modelId: string, params: ModelParam[]): void {
  lsSet(MODEL_KEY, modelId);
  lsSet(PARAMS_KEY, JSON.stringify(params));
}

function findModel(catalog: CatalogModel[], id: string): CatalogModel | undefined {
  return catalog.find((m) => m.id === id) || catalog.find((m) => isAuto(m.id));
}

function isAuto(id: string): boolean {
  return id === "default" || id === "auto" || id === "auto-smart";
}

function hasParam(model: CatalogModel | undefined, id: string): boolean {
  return Boolean(model?.parameters?.some((p) => p.id === id));
}

function effortParam(model: CatalogModel | undefined): ModelParameter | undefined {
  return model?.parameters?.find((p) => p.id === "effort") || model?.parameters?.find((p) => p.id === "reasoning");
}

function optimizeParam(model: CatalogModel | undefined): ModelParameter | undefined {
  return model?.parameters?.find((p) => p.id === "optimize_for");
}

function paramValue(params: ModelParam[], id: string): string {
  return String(params.find((p) => p.id === id)?.value || "");
}

function defaultParam(model: CatalogModel | undefined, id: string): string {
  return defaultParams(model).find((p) => p.id === id)?.value || "";
}

function setParam(params: ModelParam[], id: string, value: string): ModelParam[] {
  return [...params.filter((p) => p.id !== id), { id, value }];
}

function valueLabel(param: ModelParameter, value: string): string {
  const row = (param.values || []).find((v) => v.value === value);
  return String(row?.display_name || row?.displayName || "").replace(/\u200b/g, "");
}

function defaultLabel(model: CatalogModel): string {
  return statusLabel(model, defaultParams(model));
}

function checkSvg(): HTMLElement {
  const n = el("span", "kb-mp-check");
  n.innerHTML = `<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path fill="currentColor" d="M6.2 11.4 2.8 8l1.1-1.1 2.3 2.3 5.9-5.9L13.2 4z"/></svg>`;
  return n;
}

function lsGet(key: string): string {
  try {
    return window.localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function lsSet(key: string, value: string): void {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}
