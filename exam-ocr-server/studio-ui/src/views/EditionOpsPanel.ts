/**
 * Center workspace for 「方案」ribbon — guided edit console.
 * Left = scheme list (+ 新建) · Center = form + JSON · Right = field tutorials
 */
import { editionOps, type EditionSummary } from "../api/editionOps";
import {
  improvementPoints,
  patterns,
  LANDING_TARGETS,
  type ImprovementPattern,
  type ImprovementPoint,
} from "../api/improvementPoints";
import { store } from "../state/store";
import { el } from "../util/dom";
import { coerceSubjectForGrade, subjectOptionsForGrade } from "../util/labels";
import {
  FACET_OPTIONS,
  FORM_FIELDS,
  SEVERITY_OPTIONS,
  getByPath,
  guideForJsonPathHint,
  setByPath,
  type FieldGuide,
  type PolicyEntryDraft,
} from "./editionFieldGuide";

type PolicyRowHost = HTMLElement & { remountPolicy?: () => void };

type Tab = "edit" | "corrections" | "improvements" | "check";

/** Shared selection so left rail / right inspector stay in sync. */
let preferredEditionId = "";

export function selectEditionInOps(editionId: string): void {
  preferredEditionId = editionId;
  document.dispatchEvent(
    new CustomEvent("studio:edition-ops-select", { detail: { editionId } }),
  );
}

export function getPreferredEditionId(): string {
  return preferredEditionId;
}

export function mountEditionOpsPanel(root: HTMLElement): { refresh: () => void } {
  root.innerHTML = "";
  root.classList.add("edition-ops-panel");

  const header = el("div", "edition-ops-header");
  const title = el("h2", "edition-ops-title", "编辑方案");
  header.appendChild(title);
  header.appendChild(
    el("p", "muted edition-ops-lead", "左侧选方案 · 中间用表单/JSON 编辑 · 右侧看字段说明"),
  );
  root.appendChild(header);

  const tabs = el("div", "edition-ops-tabs");
  const body = el("div", "edition-ops-body");
  root.appendChild(tabs);
  root.appendChild(body);

  let active: Tab = "edit";
  /** Left「新建」临时打开创建表单，不是常驻 Tab */
  let showCreate = false;
  const tabBtns = new Map<Tab, HTMLButtonElement>();

  const TAB_LABELS: { id: Tab; label: string; hint: string }[] = [
    { id: "edit", label: "编辑", hint: "改配置并保存/发布" },
    { id: "corrections", label: "待审建议", hint: "导入反馈生成的规则候选" },
    {
      id: "improvements",
      label: "改进点",
      hint: "位点簇 / 有效集 / 冲突 / 模式（accepted+procedure）",
    },
    { id: "check", label: "检查", hint: "健康检查配置是否完整" },
  ];

  for (const t of TAB_LABELS) {
    const btn = el("button", "btn-secondary", t.label) as HTMLButtonElement;
    btn.type = "button";
    btn.title = t.hint;
    btn.onclick = () => {
      showCreate = false;
      active = t.id;
      syncTabs();
      void render();
    };
    tabBtns.set(t.id, btn);
    tabs.appendChild(btn);
  }

  const syncTabs = () => {
    tabBtns.forEach((btn, id) => btn.classList.toggle("active", !showCreate && id === active));
  };

  const syncTitle = (editions: EditionSummary[]) => {
    if (showCreate) {
      title.textContent = "新建方案";
      return;
    }
    if (!preferredEditionId) {
      title.textContent = "编辑方案";
      return;
    }
    const hit = editions.find((e) => e.id === preferredEditionId);
    title.textContent = hit ? `编辑 · ${hit.display_name || hit.id}` : `编辑 · ${preferredEditionId}`;
  };

  const render = async () => {
    body.innerHTML = "<p class='muted'>加载中…</p>";
    try {
      if (showCreate) {
        syncTitle([]);
        renderCreate(body, () => {
          showCreate = false;
          active = "edit";
          syncTabs();
          void render();
        });
        return;
      }
      if (active === "edit") await renderEdit(body, syncTitle);
      else if (active === "corrections") await renderCorrections(body);
      else if (active === "improvements") await renderImprovements(body);
      else if (active === "check") await renderCheck(body);
    } catch (err) {
      body.innerHTML = "";
      body.appendChild(el("p", "error", String(err)));
    }
  };

  document.addEventListener("studio:edition-ops-select", ((e: CustomEvent) => {
    preferredEditionId = String(e.detail?.editionId || "");
    showCreate = false;
    active = "edit";
    syncTabs();
    void render();
  }) as EventListener);

  document.addEventListener("studio:edition-ops-open-create", (() => {
    showCreate = true;
    syncTabs();
    void render();
  }) as EventListener);

  document.addEventListener("studio:edition-ops-goto-corrections", (() => {
    showCreate = false;
    active = "corrections";
    syncTabs();
    void render();
  }) as EventListener);

  document.addEventListener("studio:edition-ops-goto-improvements", (() => {
    showCreate = false;
    active = "improvements";
    syncTabs();
    void render();
  }) as EventListener);

  document.addEventListener("studio:edition-ops-saved", (() => {
    if (!showCreate && active === "edit") void render();
  }) as EventListener);

  void editionOps.list().then(({ editions }) => {
    if (!preferredEditionId && editions[0]) {
      selectEditionInOps(editions[0].id);
    } else {
      syncTabs();
      void render();
    }
  });

  return { refresh: () => void render() };
}

function focusField(path: string): void {
  document.dispatchEvent(
    new CustomEvent("studio:edition-field-focus", { detail: { path } }),
  );
}

async function renderEdit(
  body: HTMLElement,
  syncTitle: (editions: EditionSummary[]) => void,
) {
  body.innerHTML = "";
  body.classList.add("edition-ops-edit-workspace");
  if (!preferredEditionId) {
    body.appendChild(el("p", "muted edition-ops-empty", "请从左侧选择一个方案"));
    return;
  }

  const { editions } = await editionOps.list();
  syncTitle(editions);
  const e = editions.find((x) => x.id === preferredEditionId);
  if (!e) {
    body.appendChild(el("p", "muted", "方案不存在或已删除"));
    return;
  }

  const draft = await editionOps.draft(preferredEditionId);
  let bundle = structuredClone(draft.bundle || {}) as Record<string, unknown>;
  // Coerce subject against grade on load (legacy drafts may mismatch).
  {
    const grade = String(getByPath(bundle, "meta.grade_band") || "hs");
    const subj = String(getByPath(bundle, "meta.subject") || "");
    const fixed = coerceSubjectForGrade(subj, grade);
    if (fixed !== subj) setByPath(bundle, "meta.subject", fixed);
  }

  const toolbar = el("div", "edition-ops-toolbar edition-ops-toolbar-sticky");
  const saveBtn = el("button", "btn-secondary", "仅保存草稿") as HTMLButtonElement;
  const pubBtn = el("button", "btn-primary", "保存并发布") as HTMLButtonElement;
  saveBtn.type = "button";
  pubBtn.type = "button";
  const status = el("p", "muted edition-ops-status-inline", "");
  toolbar.append(saveBtn, pubBtn, status);
  body.appendChild(toolbar);

  body.appendChild(
    el(
      "p",
      "muted edition-ops-edit-intro",
      `标识 ${e.id}（只读）· 已发布 ${e.published_version || "—"} · 点字段看右侧说明；枚举请用下拉，勿乱填。`,
    ),
  );

  const form = el("div", "edition-ops-form");
  const controls = new Map<string, HTMLElement>();

  const sections: { title: string; match: (path: string) => boolean }[] = [
    { title: "基本信息（meta）", match: (p) => p.startsWith("meta.") },
    {
      title: "解析模式（refine）",
      match: (p) => p.startsWith("config.refine"),
    },
    { title: "版式特征（features）", match: (p) => p.startsWith("config.features.") },
    { title: "分栏坐标（geometry）", match: (p) => p.startsWith("config.geometry.") },
    { title: "识别正则（patterns）", match: (p) => p.startsWith("config.patterns.") },
    {
      title: "遗留扁平配置（zones / figure_group）",
      match: (p) => p === "config.zones" || p === "config.figure_group",
    },
    { title: "软规则（policy）", match: (p) => p.startsWith("policy.") },
    { title: "资源（assets）", match: (p) => p.startsWith("assets.") },
  ];

  for (const sec of sections) {
    const fields = FORM_FIELDS.filter((f) => sec.match(f.path));
    if (!fields.length) continue;
    const box = el("section", "edition-ops-form-section");
    box.appendChild(el("h3", "edition-ops-h3", sec.title));
    for (const f of fields) {
      const row = buildFormRow(f, bundle, (path, value) => {
        setByPath(bundle, path, value);
        if (path === "meta.grade_band") {
          const next = coerceSubjectForGrade(
            String(getByPath(bundle, "meta.subject") || ""),
            String(value),
          );
          setByPath(bundle, "meta.subject", next);
          refillSubjectSelect(controls, bundle);
        }
        const touchedLegacy = applyLegacySync(bundle, path, value);
        syncJsonFromBundle();
        // Only refresh sibling controls when legacy flat keys were rewritten.
        if (touchedLegacy) syncFormFromBundle();
        else if (path === "meta.grade_band") syncFormFromBundle();
        focusField(path);
      });
      controls.set(f.path, row);
      box.appendChild(row);
    }
    form.appendChild(box);
  }
  body.appendChild(form);
  refillSubjectSelect(controls, bundle);

  body.appendChild(el("h3", "edition-ops-h3", "完整配置 JSON"));
  body.appendChild(
    el(
      "p",
      "muted",
      "始终展开。表单改动能同步到此；也可直接改（光标处字段会更新右侧教程）。保存以这里的 JSON 为准。",
    ),
  );
  const editor = el("textarea", "edition-ops-editor edition-ops-editor-full") as HTMLTextAreaElement;
  editor.spellcheck = false;
  editor.value = JSON.stringify(bundle, null, 2);
  const fitEditor = () => {
    const lines = editor.value.split("\n").length;
    editor.rows = Math.max(28, lines + 2);
  };
  fitEditor();
  body.appendChild(editor);

  let syncing = false;
  function syncJsonFromBundle(): void {
    syncing = true;
    editor.value = JSON.stringify(bundle, null, 2);
    fitEditor();
    syncing = false;
  }

  function syncFormFromBundle(): void {
    refillSubjectSelect(controls, bundle);
    for (const f of FORM_FIELDS) {
      const row = controls.get(f.path) as PolicyRowHost | undefined;
      if (!row) continue;
      if (f.control === "policy_entries") {
        row.remountPolicy?.();
        continue;
      }
      const v = getByPath(bundle, f.path);
      const ta = row.querySelector<HTMLTextAreaElement>("textarea");
      if (ta) {
        if (f.control === "stringlist") {
          ta.value = Array.isArray(v) ? v.map(String).join("\n") : "";
        } else if (f.control === "json") {
          ta.value = v == null ? "{}" : JSON.stringify(v, null, 2);
        }
        continue;
      }
      const input = row.querySelector<HTMLInputElement | HTMLSelectElement>(
        "input, select",
      );
      if (!input) continue;
      if (f.control === "toggle") {
        input.value = v ? "true" : "false";
      } else if (f.control === "readonly") {
        input.value = v != null ? String(v) : "";
      } else if (v != null) {
        input.value = String(v);
      } else if (f.control === "regex" || f.control === "text") {
        input.value = "";
      }
    }
  }

  editor.addEventListener("focus", () => focusField(""));
  editor.addEventListener("click", () => {
    const g = guideForJsonPathHint(editor.value, editor.selectionStart || 0);
    focusField(g?.path || "");
  });
  editor.addEventListener("keyup", () => {
    const g = guideForJsonPathHint(editor.value, editor.selectionStart || 0);
    if (g) focusField(g.path);
  });
  editor.addEventListener("input", () => {
    if (syncing) return;
    fitEditor();
    try {
      bundle = JSON.parse(editor.value) as Record<string, unknown>;
      const grade = String(getByPath(bundle, "meta.grade_band") || "hs");
      const fixed = coerceSubjectForGrade(
        String(getByPath(bundle, "meta.subject") || ""),
        grade,
      );
      if (fixed !== String(getByPath(bundle, "meta.subject") || "")) {
        setByPath(bundle, "meta.subject", fixed);
        syncJsonFromBundle();
      }
      syncFormFromBundle();
      status.textContent = "JSON 已解析 · 未保存";
      status.classList.remove("error");
    } catch {
      status.textContent = "JSON 格式无效 — 保存前请修好";
      status.classList.add("error");
    }
  });

  status.textContent = draft.updated_at
    ? `草稿已加载 · ${draft.updated_at}`
    : "草稿已加载";

  document.dispatchEvent(
    new CustomEvent("studio:edition-ops-loaded", { detail: { editionId: preferredEditionId } }),
  );
  focusField("");

  const readBundle = (): Record<string, unknown> => {
    const parsed = JSON.parse(editor.value) as Record<string, unknown>;
    bundle = parsed;
    return parsed;
  };

  saveBtn.onclick = async () => {
    try {
      const b = readBundle();
      await editionOps.saveDraft(preferredEditionId, b, "studio");
      status.textContent = "草稿已保存（导入仍用已发布版）";
      status.classList.remove("error");
      document.dispatchEvent(
        new CustomEvent("studio:edition-ops-saved", { detail: { editionId: preferredEditionId } }),
      );
    } catch (err) {
      status.textContent = String(err);
      status.classList.add("error");
    }
  };
  pubBtn.onclick = async () => {
    if (!confirm("发布后，之后的新 PDF 导入将使用此方案。确定发布？")) return;
    try {
      const b = readBundle();
      await editionOps.saveDraft(preferredEditionId, b, "studio");
      const res = await editionOps.publish(preferredEditionId, "studio publish");
      status.textContent = `已发布 · 版本 ${(res as { version?: string }).version || "?"}`;
      status.classList.remove("error");
      document.dispatchEvent(
        new CustomEvent("studio:edition-ops-saved", { detail: { editionId: preferredEditionId } }),
      );
    } catch (err) {
      status.textContent = String(err);
      status.classList.add("error");
    }
  };
}

/** Rebuild meta.subject <select> options from current meta.grade_band. */
function refillSubjectSelect(
  controls: Map<string, HTMLElement>,
  bundle: Record<string, unknown>,
): void {
  const row = controls.get("meta.subject");
  if (!row) return;
  const sel = row.querySelector<HTMLSelectElement>("select");
  if (!sel) return;
  const grade = String(getByPath(bundle, "meta.grade_band") || "hs");
  const opts = subjectOptionsForGrade(grade);
  const want = coerceSubjectForGrade(String(getByPath(bundle, "meta.subject") || ""), grade);
  sel.innerHTML = "";
  for (const o of opts) {
    const opt = document.createElement("option");
    opt.value = o.value;
    opt.textContent = o.label;
    sel.appendChild(opt);
  }
  sel.value = want;
  setByPath(bundle, "meta.subject", want);
}

/** Keep legacy flat keys aligned with geometry / pattern knobs. */
function applyLegacySync(
  bundle: Record<string, unknown>,
  path: string,
  value: unknown,
): boolean {
  let touched = false;
  if (path === "config.geometry.aside_left_x1_max") {
    setByPath(bundle, "config.zones.aside_left_x1_max", value);
    touched = true;
  }
  if (path === "config.geometry.aside_right_x0_min") {
    setByPath(bundle, "config.zones.aside_right_x0_min", value);
    touched = true;
  }
  if (path === "config.geometry.figure_y_tolerance") {
    setByPath(bundle, "config.figure_group.y_tolerance", value);
    touched = true;
  }
  if (path === "config.patterns.figure_caption") {
    const s = String(value ?? "").trim();
    if (s) setByPath(bundle, "config.figure_group.caption_pattern", s);
    else {
      const fg = getByPath(bundle, "config.figure_group");
      if (fg && typeof fg === "object" && !Array.isArray(fg)) {
        const next = { ...(fg as Record<string, unknown>) };
        delete next.caption_pattern;
        setByPath(bundle, "config.figure_group", next);
      }
    }
    touched = true;
  }
  return touched;
}

function normalizePolicyEntries(raw: unknown): PolicyEntryDraft[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item, i) => {
    const o = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
    const tags = Array.isArray(o.tags)
      ? o.tags.map(String).filter(Boolean)
      : String(o.tags || "")
          .split(/[,，\s]+/)
          .map((t) => t.trim())
          .filter(Boolean);
    return {
      id: String(o.id || `entry_${i + 1}`),
      facet: String(o.facet || "content_correct"),
      severity: String(o.severity || "should"),
      statement: String(o.statement || ""),
      tags,
    };
  });
}

function buildPolicyEntriesEditor(
  bundle: Record<string, unknown>,
  onChange: (entries: PolicyEntryDraft[]) => void,
): PolicyRowHost {
  const wrap = el("div", "edition-policy-editor") as PolicyRowHost;
  const toolbar = el("div", "edition-policy-toolbar");
  const count = el("span", "muted edition-policy-count", "");
  const addBtn = el("button", "btn-secondary", "+ 添加规则") as HTMLButtonElement;
  addBtn.type = "button";
  toolbar.append(count, addBtn);
  const list = el("div", "edition-policy-list");
  wrap.append(toolbar, list);

  const read = () => normalizePolicyEntries(getByPath(bundle, "policy.entries"));

  const commit = (entries: PolicyEntryDraft[], remount = false) => {
    onChange(entries);
    count.textContent = `${entries.length} 条`;
    if (remount) remountList();
  };

  const remountList = () => {
    const entries = read();
    count.textContent = `${entries.length} 条`;
    list.innerHTML = "";
    if (!entries.length) {
      list.appendChild(
        el("p", "muted edition-policy-empty", "暂无软规则。点「添加规则」或从待审建议通过后写入。"),
      );
      return;
    }
    const total = entries.length;
    entries.forEach((entry, index) => {
      list.appendChild(
        buildPolicyCard(entry, index, total, {
          getAll: read,
          commit,
        }),
      );
    });
  };

  addBtn.onclick = () => {
    const entries = read();
    const n = entries.length + 1;
    entries.push({
      id: `rule_${n}`,
      facet: "content_correct",
      severity: "should",
      statement: "",
      tags: [],
    });
    commit(entries, true);
    focusField("policy.entries");
  };

  wrap.remountPolicy = remountList;
  remountList();
  wrap.addEventListener("pointerdown", () => focusField("policy.entries"));
  return wrap;
}

function buildPolicyCard(
  entry: PolicyEntryDraft,
  index: number,
  total: number,
  api: {
    getAll: () => PolicyEntryDraft[];
    commit: (entries: PolicyEntryDraft[], remount?: boolean) => void;
  },
): HTMLElement {
  const card = el("div", "edition-policy-card");
  const head = el("div", "edition-policy-card-head");
  head.appendChild(el("strong", "edition-policy-card-title", `#${index + 1}`));
  const actions = el("div", "edition-policy-card-actions");
  const up = el("button", "btn-secondary edition-policy-icon-btn", "↑") as HTMLButtonElement;
  const down = el("button", "btn-secondary edition-policy-icon-btn", "↓") as HTMLButtonElement;
  const del = el("button", "btn-secondary edition-policy-icon-btn", "删除") as HTMLButtonElement;
  up.type = down.type = del.type = "button";
  up.title = "上移";
  down.title = "下移";
  up.disabled = index === 0;
  down.disabled = index >= total - 1;
  up.onclick = () => {
    if (index <= 0) return;
    const next = [...api.getAll()];
    [next[index - 1], next[index]] = [next[index], next[index - 1]];
    api.commit(next, true);
  };
  down.onclick = () => {
    const all = api.getAll();
    if (index >= all.length - 1) return;
    const next = [...all];
    [next[index + 1], next[index]] = [next[index], next[index + 1]];
    api.commit(next, true);
  };
  del.onclick = () => {
    if (!confirm(`删除规则「${entry.id || `#${index + 1}`}」？`)) return;
    api.commit(
      api.getAll().filter((_, i) => i !== index),
      true,
    );
  };
  actions.append(up, down, del);
  head.appendChild(actions);
  card.appendChild(head);

  const patch = (key: keyof PolicyEntryDraft, value: string | string[]) => {
    const next = api.getAll().map((e, i) => (i === index ? { ...e, [key]: value } : e));
    api.commit(next, false);
  };

  card.appendChild(
    policyField("标识 id", () => {
      const input = el("input", "edition-ops-input edition-ops-input-mono") as HTMLInputElement;
      input.value = entry.id;
      input.placeholder = "hj.zone.aside_right";
      input.oninput = () => patch("id", input.value.trim());
      return input;
    }),
  );

  card.appendChild(
    policyField("主题 facet", () => {
      const sel = el("select", "edition-ops-input") as HTMLSelectElement;
      for (const o of FACET_OPTIONS) {
        const opt = document.createElement("option");
        opt.value = o.value;
        opt.textContent = o.label;
        sel.appendChild(opt);
      }
      if (![...sel.options].some((o) => o.value === entry.facet)) {
        const opt = document.createElement("option");
        opt.value = entry.facet;
        opt.textContent = `${entry.facet}（非标准）`;
        sel.appendChild(opt);
      }
      sel.value = entry.facet;
      sel.onchange = () => patch("facet", sel.value);
      return sel;
    }),
  );

  card.appendChild(
    policyField("强度 severity", () => {
      const sel = el("select", "edition-ops-input") as HTMLSelectElement;
      for (const o of SEVERITY_OPTIONS) {
        const opt = document.createElement("option");
        opt.value = o.value;
        opt.textContent = o.label;
        sel.appendChild(opt);
      }
      if (![...sel.options].some((o) => o.value === entry.severity)) {
        const opt = document.createElement("option");
        opt.value = entry.severity;
        opt.textContent = `${entry.severity}（非标准）`;
        sel.appendChild(opt);
      }
      sel.value = entry.severity;
      sel.onchange = () => patch("severity", sel.value);
      return sel;
    }),
  );

  card.appendChild(
    policyField("陈述 statement", () => {
      const ta = el("textarea", "edition-ops-input") as HTMLTextAreaElement;
      ta.rows = 3;
      ta.value = entry.statement;
      ta.placeholder = "用自然语言写给模型的规则…";
      ta.oninput = () => patch("statement", ta.value);
      return ta;
    }),
  );

  card.appendChild(
    policyField("标签 tags", () => {
      const input = el("input", "edition-ops-input") as HTMLInputElement;
      input.value = entry.tags.join(", ");
      input.placeholder = "aside, toc（逗号分隔）";
      input.oninput = () => {
        const tags = input.value
          .split(/[,，]/)
          .map((t) => t.trim())
          .filter(Boolean);
        patch("tags", tags);
      };
      return input;
    }),
  );

  return card;
}

function policyField(label: string, make: () => HTMLElement): HTMLElement {
  const row = el("div", "edition-policy-field");
  row.appendChild(el("label", "edition-policy-field-label", label));
  row.appendChild(make());
  return row;
}

function buildFormRow(
  f: FieldGuide,
  bundle: Record<string, unknown>,
  onChange: (path: string, value: unknown) => void,
): HTMLElement {
  const row = el("div", "edition-ops-form-row") as PolicyRowHost;
  if (f.control === "json" || f.control === "stringlist" || f.control === "policy_entries") {
    row.classList.add("edition-ops-form-row--wide");
  }
  if (f.control === "policy_entries") {
    row.classList.add("edition-ops-form-row--policy");
  }
  row.dataset.path = f.path;
  const label = el("label", "edition-ops-form-label", f.label);
  label.htmlFor = `ed-field-${f.path.replace(/\./g, "-")}`;
  row.appendChild(label);
  row.appendChild(el("span", "muted edition-ops-form-hint", f.hint));

  const cur = getByPath(bundle, f.path);
  let control: HTMLElement;

  if (f.control === "select" && f.options) {
    const sel = el("select", "edition-ops-input") as HTMLSelectElement;
    sel.id = label.htmlFor;
    const options =
      f.path === "meta.subject"
        ? subjectOptionsForGrade(String(getByPath(bundle, "meta.grade_band") || "hs"))
        : f.options;
    for (const o of options) {
      const opt = document.createElement("option");
      opt.value = o.value;
      opt.textContent = o.label;
      sel.appendChild(opt);
    }
    let val = String(cur ?? options[0]?.value ?? "");
    if (f.path === "meta.subject") {
      val = coerceSubjectForGrade(val, String(getByPath(bundle, "meta.grade_band") || "hs"));
    }
    if (![...sel.options].some((o) => o.value === val)) {
      const opt = document.createElement("option");
      opt.value = val;
      opt.textContent = `${val}（当前值，非本学段）`;
      sel.appendChild(opt);
    }
    sel.value = val;
    sel.onchange = () => onChange(f.path, sel.value);
    sel.onfocus = () => focusField(f.path);
    control = sel;
  } else if (f.control === "toggle") {
    const sel = el("select", "edition-ops-input") as HTMLSelectElement;
    sel.id = label.htmlFor;
    sel.innerHTML = '<option value="true">是</option><option value="false">否</option>';
    sel.value = cur ? "true" : "false";
    sel.onchange = () => onChange(f.path, sel.value === "true");
    sel.onfocus = () => focusField(f.path);
    control = sel;
  } else if (f.control === "number") {
    const input = el("input", "edition-ops-input") as HTMLInputElement;
    input.id = label.htmlFor;
    input.type = "number";
    if (f.min != null) input.min = String(f.min);
    if (f.max != null) input.max = String(f.max);
    if (f.step != null) input.step = String(f.step);
    input.value = cur != null ? String(cur) : "";
    input.oninput = () => {
      const n = Number(input.value);
      if (!Number.isNaN(n)) onChange(f.path, n);
    };
    input.onfocus = () => focusField(f.path);
    control = input;
  } else if (f.control === "readonly") {
    const input = el("input", "edition-ops-input") as HTMLInputElement;
    input.id = label.htmlFor;
    input.type = "text";
    input.readOnly = true;
    input.classList.add("edition-ops-input-readonly");
    input.value = cur != null ? String(cur) : "";
    input.onfocus = () => focusField(f.path);
    control = input;
  } else if (f.control === "stringlist") {
    const ta = el("textarea", "edition-ops-input edition-ops-input-mono") as HTMLTextAreaElement;
    ta.id = label.htmlFor;
    ta.rows = f.rows ?? 3;
    ta.spellcheck = false;
    ta.value = Array.isArray(cur) ? cur.map(String).join("\n") : "";
    ta.oninput = () => {
      const lines = ta.value
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      onChange(f.path, lines);
    };
    ta.onfocus = () => focusField(f.path);
    control = ta;
  } else if (f.control === "policy_entries") {
    const editor = buildPolicyEntriesEditor(bundle, (entries) => {
      onChange(f.path, entries);
    });
    row.remountPolicy = () => editor.remountPolicy?.();
    control = editor;
  } else if (f.control === "json") {
    const ta = el("textarea", "edition-ops-input edition-ops-input-mono") as HTMLTextAreaElement;
    ta.id = label.htmlFor;
    ta.rows = f.rows ?? 6;
    ta.spellcheck = false;
    ta.value = cur == null ? "{}" : JSON.stringify(cur, null, 2);
    ta.oninput = () => {
      try {
        const parsed = JSON.parse(ta.value || "{}");
        ta.classList.remove("edition-ops-input-invalid");
        onChange(f.path, parsed);
      } catch {
        ta.classList.add("edition-ops-input-invalid");
      }
    };
    ta.onfocus = () => focusField(f.path);
    control = ta;
  } else {
    const input = el("input", "edition-ops-input") as HTMLInputElement;
    input.id = label.htmlFor;
    input.type = "text";
    if (f.control === "regex") input.classList.add("edition-ops-input-mono");
    input.value = cur != null ? String(cur) : "";
    input.oninput = () => onChange(f.path, input.value);
    input.onfocus = () => focusField(f.path);
    control = input;
  }

  row.appendChild(control);
  row.addEventListener("pointerdown", () => focusField(f.path));
  return row;
}

async function renderImprovements(body: HTMLElement) {
  body.innerHTML = "";
  body.appendChild(el("h3", "edition-ops-h3", "改进点（快捷）"));
  const jump = el("p", "muted");
  jump.appendChild(
    document.createTextNode(
      "完整管理平台在左侧 Ribbon「改进点」。此处为方案上下文快捷视图。" +
        (preferredEditionId ? ` 当前方案：${preferredEditionId}` : " "),
    ),
  );
  const jumpBtn = el("button", "btn-secondary", "打开改进点管理平台") as HTMLButtonElement;
  jumpBtn.type = "button";
  jumpBtn.style.marginLeft = "0.5rem";
  jumpBtn.onclick = () => {
    store.setActiveRibbon("improvements");
    document.dispatchEvent(new CustomEvent("studio:improvements-refresh"));
  };
  jump.appendChild(jumpBtn);
  body.appendChild(jump);
  body.appendChild(
    el(
      "p",
      "muted",
      "仅 accepted 可沉淀；含成功路径 procedure。冲突须裁决后才能 finalize。",
    ),
  );

  const jobInput = el("input", "edition-ops-input") as HTMLInputElement;
  jobInput.placeholder = "可选：按 job_id 筛选";
  jobInput.style.maxWidth = "28rem";
  const fpInput = el("input", "edition-ops-input") as HTMLInputElement;
  fpInput.placeholder = "可选：fingerprint_hash";
  fpInput.style.maxWidth = "28rem";
  const reloadBtn = el("button", "btn-secondary", "刷新") as HTMLButtonElement;
  reloadBtn.type = "button";
  const toolbar = el("div", "edition-ops-toolbar");
  toolbar.append(jobInput, fpInput, reloadBtn);
  body.appendChild(toolbar);

  type ViewFilter = "accepted" | "conflicts" | "all" | "patterns";
  let view: ViewFilter = "accepted";
  const viewRow = el("div", "edition-ops-toolbar");
  const mkView = (id: ViewFilter, label: string) => {
    const b = el("button", "btn-secondary", label) as HTMLButtonElement;
    b.type = "button";
    b.onclick = () => {
      view = id;
      void paint();
    };
    return b;
  };
  viewRow.append(
    mkView("accepted", "有效集"),
    mkView("conflicts", "冲突"),
    mkView("all", "按位点簇"),
    mkView("patterns", "模式桶"),
  );
  body.appendChild(viewRow);
  const listHost = el("div", "edition-ops-corr-list");
  body.appendChild(listHost);

  const paint = async () => {
    listHost.innerHTML = "<p class='muted'>加载中…</p>";
    if (view === "patterns") {
      await paintPatterns(listHost, paint);
      return;
    }
    try {
      const data = await improvementPoints.list({
        job_id: jobInput.value.trim() || undefined,
        fingerprint_hash: fpInput.value.trim() || undefined,
      });
      listHost.innerHTML = "";
      const counts = data.counts || {
        total: 0,
        accepted: 0,
        conflict: 0,
        locus: 0,
      };
      listHost.appendChild(
        el(
          "p",
          "muted",
          `合计 ${counts.total} · accepted ${counts.accepted} · conflict ${counts.conflict} · locus ${counts.locus}`,
        ),
      );

      const renderIpCard = (it: ImprovementPoint, showActions: boolean) => {
        const card = el("div", "edition-ops-corr-card");
        const claim = (it.claim || {}) as Record<string, unknown>;
        const steps = it.procedure?.steps || [];
        const stepS = steps
          .map((s) => String(s.tool || ""))
          .filter(Boolean)
          .slice(0, 6)
          .join(" → ");
        card.appendChild(
          el(
            "div",
            "edition-ops-corr-head",
            `p${it.page_no ?? "?"} · ${it.category || "?"} · ${it.status || "?"} · ${it.authority || ""}`,
          ),
        );
        card.appendChild(
          el("p", "", String(claim.summary || it.locus_key || it.id || "")),
        );
        if (stepS) {
          card.appendChild(el("p", "muted", `procedure: ${stepS}`));
        }
        if (it.pattern_id || it.pattern_status) {
          card.appendChild(
            el(
              "p",
              "muted",
              `pattern: ${it.pattern_status || "none"}${it.pattern_id ? ` · ${it.pattern_id.slice(0, 8)}…` : ""}`,
            ),
          );
        }
        if (showActions && it.status === "conflict" && it.id) {
          const row = el("div", "edition-ops-toolbar");
          const win = el("button", "btn-primary", "判为胜出") as HTMLButtonElement;
          win.type = "button";
          win.onclick = async () => {
            await improvementPoints.resolveConflict(it.id, true);
            await paint();
          };
          const lose = el("button", "btn-secondary", "否决") as HTMLButtonElement;
          lose.type = "button";
          lose.onclick = async () => {
            await improvementPoints.resolveConflict(it.id, false);
            await paint();
          };
          row.append(win, lose);
          card.appendChild(row);
        }
        if (showActions && it.status === "accepted" && it.id) {
          const row = el("div", "edition-ops-toolbar");
          const noneBtn = el("button", "btn-secondary", "仅专书(不抽象)") as HTMLButtonElement;
          noneBtn.type = "button";
          noneBtn.onclick = async () => {
            await improvementPoints.setPattern(it.id, { pattern_status: "none" });
            await paint();
          };
          const draftBtn = el("button", "btn-secondary", "抽象为模式草案") as HTMLButtonElement;
          draftBtn.type = "button";
          draftBtn.onclick = async () => {
            // 不再硬塞 target_layer："L1"：落地目标由后端按类目给先验，再到模式桶里逐条定路由。
            await improvementPoints.setPattern(it.id, {
              pattern_status: "draft",
              abstract_text: String(claim.summary || ""),
              edition_family_id: preferredEditionId || "",
            });
            await paint();
          };
          row.append(noneBtn, draftBtn);
          card.appendChild(row);
        }
        return card;
      };

      if (view === "conflicts") {
        const items = data.conflicts || [];
        if (!items.length) {
          listHost.appendChild(el("p", "muted", "无未决冲突。"));
        }
        for (const it of items) listHost.appendChild(renderIpCard(it, true));
        return;
      }
      if (view === "accepted") {
        const items = data.accepted || [];
        if (!items.length) {
          listHost.appendChild(el("p", "muted", "尚无 accepted 改进点。"));
        }
        for (const it of items) listHost.appendChild(renderIpCard(it, true));
        return;
      }
      // all / by locus
      const loci = data.by_locus || {};
      const keys = Object.keys(loci).sort();
      if (!keys.length) {
        listHost.appendChild(el("p", "muted", "无改进点。可先导入并记入待审议。"));
      }
      for (const lk of keys) {
        const cluster = loci[lk] || [];
        listHost.appendChild(
          el("h4", "edition-ops-h3", `${lk} · ${cluster.length} 条`),
        );
        for (const it of cluster) listHost.appendChild(renderIpCard(it, true));
      }
    } catch (err) {
      listHost.innerHTML = "";
      listHost.appendChild(el("p", "error", String(err)));
    }
  };

  reloadBtn.onclick = () => void paint();
  await paint();
}

const PATTERN_STATUS_LABEL: Record<string, string> = {
  draft: "草案",
  candidate: "待定路由",
  routed: "已定路由 · 待落地",
  landed: "已落地",
  no_landing: "不可规则化",
  needs_ip_split: "需先拆改进点",
  approved: "已批准（历史）",
};

/** 模式桶：模式级视图 —— 列成员页、定路由、落地记账。 */
async function paintPatterns(host: HTMLElement, repaint: () => Promise<void>) {
  try {
    const data = await patterns.list({ with_members: true, limit: 300 });
    host.innerHTML = "";
    const byStatus = data.counts?.by_status || {};
    const byTarget = data.counts?.by_landing_target || {};
    host.appendChild(
      el(
        "p",
        "muted",
        `模式 ${Object.values(byStatus).reduce((a, b) => a + b, 0)} 条 · ` +
          Object.entries(byStatus)
            .map(([k, v]) => `${PATTERN_STATUS_LABEL[k] || k} ${v}`)
            .join(" · "),
      ),
    );
    host.appendChild(
      el(
        "p",
        "muted",
        "落地目标分布：" +
          Object.entries(byTarget)
            .map(([k, v]) => `${k} ${v}`)
            .join(" · "),
      ),
    );

    const list = data.patterns || [];
    if (!list.length) {
      host.appendChild(el("p", "muted", "尚无抽象模式。"));
      return;
    }
    for (const pat of list) {
      host.appendChild(renderPatternCard(pat, repaint));
    }
  } catch (err) {
    host.innerHTML = "";
    host.appendChild(el("p", "error", String(err)));
  }
}

function renderPatternCard(
  pat: ImprovementPattern,
  repaint: () => Promise<void>,
): HTMLElement {
  const card = el("div", "edition-ops-corr-card");
  const status = String(pat.status || "draft");
  card.appendChild(
    el(
      "div",
      "edition-ops-corr-head",
      `${pat.category || "?"} · 成员 ${pat.member_count ?? 0} · ${PATTERN_STATUS_LABEL[status] || status}`,
    ),
  );
  card.appendChild(el("p", "", String(pat.abstract_text || pat.id)));

  const pages = pat.member_pages || [];
  if (pages.length) {
    // 成员页 = 这条模式的黄金集：落地后这些页应当改善，其余页不得变差。
    const shown = pages.slice(0, 24).join(", ");
    const more = pages.length > 24 ? ` …共 ${pages.length} 页` : "";
    card.appendChild(el("p", "muted", `成员页（黄金集）：${shown}${more}`));
  }
  card.appendChild(
    el(
      "p",
      "muted",
      `落地目标：${pat.landing_target || "(未定)"}${
        pat.landing_target_label ? ` — ${pat.landing_target_label}` : ""
      }`,
    ),
  );
  if (pat.landed_ref) {
    card.appendChild(el("p", "muted", `已落地于：${pat.landed_ref}`));
  }
  if (pat.landing_note) {
    card.appendChild(el("p", "muted", `判定理由：${pat.landing_note}`));
  }

  if (status !== "landed") {
    const row = el("div", "edition-ops-toolbar");
    const sel = el("select", "edition-ops-select") as HTMLSelectElement;
    const blank = el("option", "", "（选择落地目标）") as HTMLOptionElement;
    blank.value = "";
    sel.appendChild(blank);
    for (const [key, label] of Object.entries(LANDING_TARGETS)) {
      const opt = el("option", "", `${key} — ${label}`) as HTMLOptionElement;
      opt.value = key;
      if (key === pat.landing_target) opt.selected = true;
      sel.appendChild(opt);
    }
    const noteInput = el("input", "edition-ops-input") as HTMLInputElement;
    noteInput.placeholder = "判定理由（为什么是这个生效面）";
    noteInput.value = pat.landing_note || "";
    const routeBtn = el("button", "btn-primary", "定路由") as HTMLButtonElement;
    routeBtn.type = "button";
    routeBtn.onclick = async () => {
      if (!sel.value) return;
      await patterns.route(pat.id, sel.value, noteInput.value.trim());
      await repaint();
    };
    row.append(sel, noteInput, routeBtn);
    card.appendChild(row);
  }

  if (status === "routed") {
    const row = el("div", "edition-ops-toolbar");
    const refInput = el("input", "edition-ops-input") as HTMLInputElement;
    refInput.placeholder = "landed_ref：规则 id / config 点路径 / CSS 选择器 / 测试节点";
    const landBtn = el("button", "btn-primary", "标记已落地") as HTMLButtonElement;
    landBtn.type = "button";
    landBtn.onclick = async () => {
      const ref = refInput.value.trim();
      if (!ref) return;
      await patterns.land(pat.id, ref);
      await repaint();
    };
    row.append(refInput, landBtn);
    card.appendChild(row);
    card.appendChild(
      el(
        "p",
        "muted",
        "落地前先跑全书 rebuild_ir：成员页应改善，且全书 escalate 不得变差。",
      ),
    );
  }
  return card;
}

async function renderCorrections(body: HTMLElement) {
  const { items } = await editionOps.corrections(
    preferredEditionId || undefined,
    "pending",
  );
  body.innerHTML = "";
  body.appendChild(el("h3", "edition-ops-h3", "待审建议"));
  body.appendChild(
    el(
      "p",
      "muted",
      preferredEditionId
        ? `当前方案：${preferredEditionId}。非越权通过→并入方案 policy；越权通过→记入 pipeline_backlog（需改核心代码）。`
        : "未选方案时显示全部待审。建议先从左侧点选方案。",
    ),
  );

  type AuthFilter = "all" | "edition" | "pipeline";
  let filter: AuthFilter = "all";
  const filterRow = el("div", "edition-ops-toolbar");
  const mkFilter = (id: AuthFilter, label: string) => {
    const b = el("button", "btn-secondary", label) as HTMLButtonElement;
    b.type = "button";
    b.dataset.authFilter = id;
    b.onclick = () => {
      filter = id;
      paint();
    };
    return b;
  };
  filterRow.append(
    mkFilter("all", "全部"),
    mkFilter("edition", "非越权"),
    mkFilter("pipeline", "越权"),
  );
  body.appendChild(filterRow);
  const listHost = el("div", "edition-ops-corr-list");
  body.appendChild(listHost);

  const corrAuthority = (it: Record<string, unknown>): "edition" | "pipeline" => {
    const top = String(it.authority || "").trim();
    if (top === "pipeline" || top === "edition") return top;
    const cand = (it.candidate || {}) as Record<string, unknown>;
    if (String(cand.authority || "") === "pipeline") return "pipeline";
    const tags = Array.isArray(cand.tags) ? cand.tags.map(String) : [];
    if (tags.includes("authority:pipeline") || tags.includes("越权")) return "pipeline";
    return "edition";
  };

  const paint = () => {
    for (const b of filterRow.querySelectorAll("button")) {
      const bid = (b as HTMLButtonElement).dataset.authFilter as AuthFilter;
      b.className = bid === filter ? "btn-primary" : "btn-secondary";
    }
    listHost.innerHTML = "";
    const shown = items.filter((it) => {
      if (filter === "all") return true;
      return corrAuthority(it) === filter;
    });
    if (!shown.length) {
      listHost.appendChild(el("p", "muted", "暂无待审建议"));
      return;
    }
    for (const it of shown) {
      const card = el("div", "edition-ops-card");
      const id = String(it.id || "");
      const auth = corrAuthority(it);
      const head = el("div", "edition-ops-corr-head");
      head.appendChild(el("strong", "", String(it.title || it.kind || id)));
      const badge = el(
        "span",
        auth === "pipeline" ? "edition-ops-badge edition-ops-badge--pipeline" : "edition-ops-badge edition-ops-badge--edition",
        auth === "pipeline" ? "越权" : "非越权",
      );
      badge.title =
        auth === "pipeline"
          ? "需改 Pipeline 核心代码；通过后进入 meta.pipeline_backlog"
          : "可只靠方案数据吸收；通过后并入草稿 policy";
      head.appendChild(badge);
      card.appendChild(head);
      card.appendChild(el("p", "muted", String(it.summary || it.note || "")));
      const row = el("div", "edition-ops-toolbar");
      const ok = el(
        "button",
        "btn-primary",
        auth === "pipeline" ? "通过·记入 backlog" : "通过",
      ) as HTMLButtonElement;
      const no = el("button", "btn-secondary", "驳回") as HTMLButtonElement;
      ok.type = "button";
      no.type = "button";
      ok.onclick = async () => {
        await editionOps.reviewCorrection(id, "approve", "studio");
        await renderCorrections(body);
      };
      no.onclick = async () => {
        await editionOps.reviewCorrection(id, "reject", "studio");
        await renderCorrections(body);
      };
      row.append(ok, no);
      card.appendChild(row);
      listHost.appendChild(card);
    }
  };
  paint();
}

async function renderCheck(body: HTMLElement) {
  body.innerHTML = "";
  body.appendChild(el("h3", "edition-ops-h3", "健康检查"));
  if (!preferredEditionId) {
    body.appendChild(el("p", "muted", "请先从左侧选择方案"));
    return;
  }
  body.appendChild(el("p", "muted", `检查方案：${preferredEditionId}`));
  const out = el("pre", "edition-ops-pre", "");
  const go = el("button", "btn-secondary", "运行检查") as HTMLButtonElement;
  go.type = "button";
  go.onclick = async () => {
    out.textContent = "检查中…";
    try {
      const res = await editionOps.golden(preferredEditionId);
      out.textContent = JSON.stringify(res, null, 2);
    } catch (e) {
      out.textContent = String(e);
    }
  };
  body.append(go, out);
  go.click();
}

function renderCreate(body: HTMLElement, onDone: () => void) {
  body.innerHTML = "";
  body.appendChild(
    el("p", "muted", "从已有方案复制一份。创建后会打开编辑。"),
  );

  const src = el("input", "edition-ops-input") as HTMLInputElement;
  src.placeholder = "源方案标识";
  src.value = preferredEditionId || "hujiao_math_hs_v1";
  const idIn = el("input", "edition-ops-input") as HTMLInputElement;
  idIn.placeholder = "新标识";
  const nameIn = el("input", "edition-ops-input") as HTMLInputElement;
  nameIn.placeholder = "显示名";
  const status = el("p", "muted", "");
  const row = el("div", "edition-ops-toolbar");
  const go = el("button", "btn-primary", "创建并打开编辑") as HTMLButtonElement;
  const cancel = el("button", "btn-secondary", "取消") as HTMLButtonElement;
  go.type = "button";
  cancel.type = "button";
  go.onclick = async () => {
    try {
      const newId = idIn.value.trim();
      if (!newId) throw new Error("请填写新标识");
      await editionOps.fork(src.value.trim(), newId, nameIn.value.trim() || newId);
      preferredEditionId = newId;
      status.textContent = "已创建";
      selectEditionInOps(newId);
      onDone();
    } catch (e) {
      status.textContent = String(e);
    }
  };
  cancel.onclick = () => onDone();
  row.append(go, cancel);
  body.append(
    labeled("从方案复制", src),
    labeled("新标识", idIn),
    labeled("显示名", nameIn),
    row,
    status,
  );
}

function labeled(label: string, control: HTMLElement): HTMLElement {
  const wrap = el("div", "edition-ops-field");
  wrap.appendChild(el("label", "", label));
  wrap.appendChild(control);
  return wrap;
}
