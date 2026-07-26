/**
 * Right dock — 编辑说明 + 当前字段实时教程（只读引导）。
 */
import { editionOps } from "../api/editionOps";
import { el } from "../util/dom";
import {
  EDIT_OVERVIEW,
  guideForPath,
  type FieldGuide,
} from "./editionFieldGuide";

let host: HTMLElement | null = null;
let currentId = "";
let activePath = "";
let bodyEl: HTMLElement | null = null;

export function mountEditionOpsInspector(root: HTMLElement): void {
  host = root;
  root.classList.add("edition-ops-inspector");
  root.innerHTML = "";
  root.appendChild(el("h3", "inspector-title", "编辑引导"));
  bodyEl = el("div", "edition-ops-inspector-body");
  root.appendChild(bodyEl);
  renderOverview();

  document.addEventListener("studio:edition-ops-select", ((e: CustomEvent) => {
    const id = e.detail?.editionId;
    if (id) currentId = String(id);
    activePath = "";
    void refreshMeta();
  }) as EventListener);

  document.addEventListener("studio:edition-ops-loaded", ((e: CustomEvent) => {
    const id = e.detail?.editionId;
    if (id) currentId = String(id);
    void refreshMeta();
  }) as EventListener);

  document.addEventListener("studio:edition-ops-saved", (() => {
    void refreshMeta();
  }) as EventListener);

  document.addEventListener("studio:edition-field-focus", ((e: CustomEvent) => {
    const path = String(e.detail?.path || "");
    activePath = path;
    const g = path ? guideForPath(path) : undefined;
    if (g) renderFieldGuide(g);
    else renderOverview();
  }) as EventListener);
}

function renderOverview(): void {
  if (!bodyEl) return;
  bodyEl.innerHTML = "";

  bodyEl.appendChild(el("h4", "edition-help-h", EDIT_OVERVIEW.title));
  const ol = el("ol", "edition-help-steps");
  for (const s of EDIT_OVERVIEW.steps) ol.appendChild(el("li", "", s));
  bodyEl.appendChild(ol);

  bodyEl.appendChild(el("h4", "edition-help-h", "注意"));
  const ul = el("ul", "edition-help-dont");
  for (const s of EDIT_OVERVIEW.dont) ul.appendChild(el("li", "", s));
  bodyEl.appendChild(ul);

  bodyEl.appendChild(
    el(
      "p",
      "muted edition-help-foot",
      "在中栏点选任意字段，这里会显示该字段的规则与后果。",
    ),
  );

  if (currentId) {
    const metaHost = el("div", "edition-help-meta");
    metaHost.appendChild(el("h4", "edition-help-h", "当前方案"));
    metaHost.appendChild(el("p", "muted", currentId));
    bodyEl.appendChild(metaHost);
  }
}

function renderFieldGuide(g: FieldGuide): void {
  if (!bodyEl) return;
  bodyEl.innerHTML = "";

  bodyEl.appendChild(el("p", "edition-help-path", g.path));
  bodyEl.appendChild(el("h4", "edition-help-h", g.label));
  bodyEl.appendChild(el("p", "edition-help-control", controlLabel(g)));

  bodyEl.appendChild(el("h4", "edition-help-h", "怎么填"));
  const how = el("div", "edition-help-tutorial");
  for (const line of g.tutorial.split("\n")) {
    how.appendChild(el("p", "", line));
  }
  bodyEl.appendChild(how);

  bodyEl.appendChild(el("h4", "edition-help-h", "发布后影响"));
  bodyEl.appendChild(el("p", "", g.impact));

  if (g.tip) {
    bodyEl.appendChild(el("p", "edition-help-tip", `建议：${g.tip}`));
  }

  if (g.options?.length) {
    bodyEl.appendChild(el("h4", "edition-help-h", "可选值"));
    const ul = el("ul", "edition-help-options");
    for (const o of g.options) {
      ul.appendChild(el("li", "", `${o.value} — ${o.label}`));
    }
    bodyEl.appendChild(ul);
  }

  const back = el("button", "btn-secondary edition-help-back", "返回总说明") as HTMLButtonElement;
  back.type = "button";
  back.onclick = () => {
    activePath = "";
    renderOverview();
  };
  bodyEl.appendChild(back);
}

function controlLabel(g: FieldGuide): string {
  switch (g.control) {
    case "select":
      return "控件：下拉选择（不要手写未列出的值）";
    case "number":
      return `控件：数字${g.min != null ? `，范围 ${g.min}–${g.max ?? "∞"}` : ""}`;
    case "toggle":
      return "控件：开关（是 / 否）";
    case "regex":
      return "控件：正则字符串（勿加首尾 / ）";
    case "text":
      return "控件：文本";
    default:
      return "控件：只读";
  }
}

async function refreshMeta(): Promise<void> {
  if (activePath && guideForPath(activePath)) {
    renderFieldGuide(guideForPath(activePath)!);
    return;
  }
  renderOverview();
  if (!bodyEl || !currentId) return;
  try {
    const { editions } = await editionOps.list();
    const e = editions.find((x) => x.id === currentId);
    if (!e) return;
    const box = bodyEl.querySelector(".edition-help-meta") || el("div", "edition-help-meta");
    box.innerHTML = "";
    box.appendChild(el("h4", "edition-help-h", "当前方案"));
    box.appendChild(el("p", "", e.display_name || e.id));
    box.appendChild(el("p", "muted", e.id));
    box.appendChild(
      el("p", "muted", `已发布 ${e.published_version || "—"} · ${e.status || ""}`),
    );
    if (!box.parentElement) bodyEl.appendChild(box);
  } catch {
    /* ignore */
  }
}
