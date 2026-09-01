import { agent, type AgentTask, type AgentTaskEvent } from "./api";
import { isJob, onLive, type LiveEvent } from "./live";
import { el } from "./util/dom";

const IMPORT_DONE = "kb.import.completed";
const GROUP_ORDER = ["系统", "任务完成", "工具"];
const WEEKDAYS: readonly [string, string][] = [
  ["1", "周一"],
  ["2", "周二"],
  ["3", "周三"],
  ["4", "周四"],
  ["5", "周五"],
  ["6", "周六"],
  ["0", "周日"],
];

function field(label: string, control: HTMLElement): HTMLElement {
  const wrap = el("label", "kb-field");
  wrap.append(el("span", "kb-field-label", label), control);
  return wrap;
}

function selectOpts(host: HTMLSelectElement, pairs: readonly [string, string][]): void {
  for (const [value, text] of pairs) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    host.appendChild(o);
  }
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function modeZh(mode: string): string {
  return mode === "schedule" ? "定时" : "触发";
}

function preview(text: string, n = 42): string {
  const one = (text || "").replace(/\s+/g, " ").trim();
  if (!one) return "—";
  return one.length > n ? `${one.slice(0, n)}…` : one;
}

function triggerZh(name: string, catalog: AgentTaskEvent[]): string {
  const hit = catalog.find((ev) => ev.id === name);
  return hit?.label || name || "—";
}

function fillTriggerSelect(sel: HTMLSelectElement, events: AgentTaskEvent[], selected: string): void {
  sel.replaceChildren();
  const groups = new Map<string, AgentTaskEvent[]>();
  for (const ev of events) {
    const g = ev.group || "其它";
    const list = groups.get(g) || [];
    list.push(ev);
    groups.set(g, list);
  }
  const keys = [
    ...GROUP_ORDER.filter((k) => groups.has(k)),
    ...[...groups.keys()].filter((k) => !GROUP_ORDER.includes(k)),
  ];
  for (const key of keys) {
    const og = document.createElement("optgroup");
    og.label = key;
    for (const ev of groups.get(key) || []) {
      const o = document.createElement("option");
      o.value = ev.id;
      o.textContent = ev.label;
      og.appendChild(o);
    }
    sel.appendChild(og);
  }
  if (selected && ![...sel.options].some((o) => o.value === selected)) {
    const o = document.createElement("option");
    o.value = selected;
    o.textContent = selected;
    sel.appendChild(o);
  }
  sel.value = selected || events[0]?.id || IMPORT_DONE;
}

function clock(iso: string): string {
  const raw = (iso || "").trim();
  if (!raw) return "—";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toDateTimeLocal(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromDateTimeLocal(value: string): string {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) throw new Error("请选择有效的日期时间");
  return d.toISOString();
}

function weekdayZh(dow: string): string {
  return WEEKDAYS.find(([v]) => v === dow)?.[1] || `周${dow}`;
}

function parseCron(cron: string): {
  kind: "daily" | "weekly" | "monthly" | "unknown";
  minute: string;
  hour: string;
  day: string;
  dow: string;
} | null {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [minute, hour, day, month, dow] = parts;
  if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour)) return null;
  if (day === "*" && month === "*" && dow === "*") {
    return { kind: "daily", minute, hour, day: "1", dow: "1" };
  }
  if (day === "*" && month === "*" && /^\d$/.test(dow)) {
    return { kind: "weekly", minute, hour, day: "1", dow };
  }
  if (month === "*" && dow === "*" && /^\d{1,2}$/.test(day)) {
    return { kind: "monthly", minute, hour, day, dow: "1" };
  }
  return { kind: "unknown", minute, hour, day: day === "*" ? "1" : day, dow: dow === "*" ? "1" : dow };
}

function scheduleZh(row: AgentTask): string {
  if (!row.cron) return row.next_run ? `${clock(row.next_run)}（一次）` : "—";
  const parsed = parseCron(row.cron);
  if (!parsed || parsed.kind === "unknown") return row.cron;
  const hm = `${pad(Number(parsed.hour))}:${pad(Number(parsed.minute))}`;
  if (parsed.kind === "daily") return `每天 ${hm}`;
  if (parsed.kind === "weekly") return `每${weekdayZh(parsed.dow)} ${hm}`;
  return `每月${Number(parsed.day)}日 ${hm}`;
}

function kv(label: string, value: string): HTMLElement {
  const row = el("div", "kb-task-kv");
  row.append(el("dt", "", label), el("dd", "", value || "—"));
  return row;
}

function inputEl(type: string, value = ""): HTMLInputElement {
  const node = document.createElement("input");
  node.type = type;
  node.className = "kb-agent-input";
  node.value = value;
  return node;
}

export function mountKbTasks(host: HTMLElement): { destroy: () => void } {
  host.className = "kb-tasks hidden";
  host.append(
    el("h1", "kb-title", "任务"),
    el("p", "muted", "到点或事件发生时，把下面这段对话发给 Agent。拖入 PDF 先用 MinerU 认前三页，再交给 Agent 抽科目学段；点开始识别后才全书入库并开清洗对话。"),
  );

  const list = el("ul", "kb-task-list");
  const formTitle = el("h2", "kb-panel-title", "新建任务");
  const form = el("form", "kb-task-form");
  const titleIn = inputEl("text");
  titleIn.placeholder = "导入完成后清洗";
  const modeSel = document.createElement("select");
  modeSel.className = "kb-agent-input";
  selectOpts(modeSel, [
    ["schedule", "定时"],
    ["trigger", "触发"],
  ]);

  const repeatSel = document.createElement("select");
  repeatSel.className = "kb-agent-input";
  selectOpts(repeatSel, [
    ["daily", "每天"],
    ["weekly", "每周"],
    ["monthly", "每月"],
    ["once", "指定日期（只跑一次）"],
  ]);
  const timeIn = inputEl("time", "09:00");
  const onceIn = inputEl("datetime-local");
  const dowSel = document.createElement("select");
  dowSel.className = "kb-agent-input";
  selectOpts(dowSel, WEEKDAYS);
  const monthDaySel = document.createElement("select");
  monthDaySel.className = "kb-agent-input";
  for (let d = 1; d <= 31; d += 1) {
    const o = document.createElement("option");
    o.value = String(d);
    o.textContent = `${d} 日`;
    monthDaySel.appendChild(o);
  }

  const triggerSel = document.createElement("select");
  triggerSel.className = "kb-agent-input";
  fillTriggerSelect(triggerSel, [{ id: IMPORT_DONE, label: "导入教材完成后", group: "系统" }], IMPORT_DONE);

  const conditionIn = document.createElement("textarea");
  conditionIn.className = "kb-agent-ta kb-task-condition";
  conditionIn.rows = 2;
  conditionIn.placeholder = "例如：这本书是高中数学，且还没有清洗过。空着则事件一到就执行。";

  const messageIn = document.createElement("textarea");
  messageIn.className = "kb-agent-ta kb-task-message";
  messageIn.rows = 6;
  messageIn.placeholder =
    "例如：教材 {{book_id}} 已导入完成。请先读 page-refine 技能，再对这本书逐页清洗。";
  const save = el("button", "btn btn-primary", "新建") as HTMLButtonElement;
  const cancelEdit = el("button", "btn btn-secondary hidden", "取消编辑") as HTMLButtonElement;
  save.type = "submit";
  cancelEdit.type = "button";
  const actions = el("div", "kb-agent-actions");
  actions.append(save, cancelEdit);

  const scheduleBox = el("div", "kb-task-when hidden");
  const repeatField = field("重复", repeatSel);
  const timeField = field("时间", timeIn);
  const onceField = field("日期和时间", onceIn);
  const dowField = field("星期", dowSel);
  const monthField = field("每月几号", monthDaySel);
  scheduleBox.append(repeatField, dowField, monthField, timeField, onceField);

  const triggerBox = el("div", "kb-task-when hidden");
  triggerBox.append(
    field("事件", triggerSel),
    field("条件（给 Agent 判断，可空）", conditionIn),
  );

  form.append(
    field("名称", titleIn),
    field("方式", modeSel),
    scheduleBox,
    triggerBox,
    field("发给 Agent 的对话", messageIn),
    actions,
  );
  host.append(list, formTitle, form);

  let rows: AgentTask[] = [];
  let catalog: AgentTaskEvent[] = [];
  let viewingId = "";
  let editingId = "";
  let detail: AgentTask | null = null;
  let stopLive: (() => void) | null = null;

  function defaultOnce(): string {
    const d = new Date();
    d.setMinutes(0, 0, 0);
    d.setHours(d.getHours() + 1);
    return toDateTimeLocal(d);
  }

  function syncWhen(): void {
    const mode = modeSel.value;
    scheduleBox.classList.toggle("hidden", mode !== "schedule");
    triggerBox.classList.toggle("hidden", mode !== "trigger");
    const kind = repeatSel.value;
    const once = kind === "once";
    timeField.classList.toggle("hidden", once);
    onceField.classList.toggle("hidden", !once);
    dowField.classList.toggle("hidden", kind !== "weekly");
    monthField.classList.toggle("hidden", kind !== "monthly");
    if (once && !onceIn.value) onceIn.value = defaultOnce();
  }

  function fillSchedule(row: AgentTask | null): void {
    if (row?.mode !== "schedule") {
      repeatSel.value = "daily";
      timeIn.value = "09:00";
      dowSel.value = "1";
      monthDaySel.value = "1";
      onceIn.value = defaultOnce();
      return;
    }
    if (!row.cron && row.next_run) {
      repeatSel.value = "once";
      const d = new Date(row.next_run);
      onceIn.value = Number.isNaN(d.getTime()) ? defaultOnce() : toDateTimeLocal(d);
      return;
    }
    const parsed = parseCron(row.cron);
    if (!parsed || parsed.kind === "unknown") {
      repeatSel.value = row.next_run ? "once" : "daily";
      if (row.next_run) {
        const d = new Date(row.next_run);
        if (!Number.isNaN(d.getTime())) onceIn.value = toDateTimeLocal(d);
      }
      if (parsed) timeIn.value = `${pad(Number(parsed.hour))}:${pad(Number(parsed.minute))}`;
      return;
    }
    repeatSel.value = parsed.kind;
    timeIn.value = `${pad(Number(parsed.hour))}:${pad(Number(parsed.minute))}`;
    dowSel.value = parsed.dow;
    monthDaySel.value = String(Math.min(31, Math.max(1, Number(parsed.day) || 1)));
    if (row.next_run) {
      const d = new Date(row.next_run);
      if (!Number.isNaN(d.getTime())) onceIn.value = toDateTimeLocal(d);
    }
  }

  function fillForm(row: AgentTask | null): void {
    titleIn.value = row?.title || "";
    const mode = row?.mode === "schedule" ? "schedule" : "trigger";
    modeSel.value = mode;
    fillTriggerSelect(triggerSel, catalog, row?.trigger || IMPORT_DONE);
    conditionIn.value = row?.mode === "trigger" ? row.condition || "" : "";
    messageIn.value = row?.message || "";
    fillSchedule(row);
    syncWhen();
  }

  function buildSchedule(): { cron: string; next_run: string } {
    const kind = repeatSel.value;
    if (kind === "once") {
      if (!onceIn.value) throw new Error("请选择日期和时间");
      return { cron: "", next_run: fromDateTimeLocal(onceIn.value) };
    }
    const hm = (timeIn.value || "09:00").split(":");
    const hour = Number(hm[0] || 0);
    const minute = Number(hm[1] || 0);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) throw new Error("请选择有效时间");
    if (kind === "weekly") return { cron: `${minute} ${hour} * * ${dowSel.value}`, next_run: "" };
    if (kind === "monthly") return { cron: `${minute} ${hour} ${monthDaySel.value} * *`, next_run: "" };
    return { cron: `${minute} ${hour} * * *`, next_run: "" };
  }

  function setEdit(id: string): void {
    editingId = id;
    const row = rows.find((r) => r.id === id) || null;
    fillForm(row);
    formTitle.textContent = row ? `编辑「${row.title || row.id}」` : "新建任务";
    save.textContent = row ? "保存" : "新建";
    cancelEdit.classList.toggle("hidden", !id);
    form.scrollIntoView({ block: "nearest" });
  }

  async function openView(id: string): Promise<void> {
    viewingId = viewingId === id ? "" : id;
    detail = null;
    paint();
    if (!viewingId) return;
    try {
      detail = await agent.task(id);
    } catch {
      detail = rows.find((r) => r.id === id) || null;
    }
    paint();
  }

  async function refresh(): Promise<void> {
    try {
      rows = (await agent.tasks()).tasks || [];
    } catch {
      rows = [];
    }
    if (viewingId && !rows.some((r) => r.id === viewingId)) {
      viewingId = "";
      detail = null;
    }
    if (editingId && !rows.some((r) => r.id === editingId)) setEdit("");
    paint();
  }

  function paintView(hostEl: HTMLElement, row: AgentTask): void {
    const box = el("dl", "kb-task-view");
    const src = detail && detail.id === row.id ? detail : row;
    box.append(
      kv("编号", src.id),
      kv("名称", src.title),
      kv("状态", src.enabled ? "开" : "关"),
      kv("方式", modeZh(src.mode)),
    );
    if (src.mode === "schedule") {
      box.append(kv("定时", scheduleZh(src)), kv("下次运行", clock(src.next_run)));
    }
    if (src.mode === "trigger") {
      box.append(kv("事件", triggerZh(src.trigger, catalog)));
      if ((src.condition || "").trim()) box.append(kv("条件", src.condition || ""));
    }
    box.append(kv("上次运行", clock(src.last_run_at)));
    if (src.last_job) {
      const jobLine = `${src.last_job.status} · ${src.last_job.job_id.slice(0, 8)}`;
      box.append(kv("上次任务", src.last_job.error ? `${jobLine} · ${src.last_job.error}` : jobLine));
    } else if (src.last_job_id) {
      box.append(kv("上次任务", src.last_job_id));
    }
    if (src.builtin) box.append(kv("来源", "内置（改动写在 tasks/tasks.json）"));
    const body = (src.message || src.prompt_text || "").trim();
    if (body) {
      box.append(el("dt", "", "发给 Agent 的对话"));
      const pre = el("dd", "kb-task-prompt");
      pre.textContent = body;
      box.append(pre);
    }
    hostEl.append(box);
  }

  function paint(): void {
    list.replaceChildren();
    if (!rows.length) {
      list.append(el("li", "muted", "还没有任务。内置「导入完成→清洗」会在工作区种子里。"));
      return;
    }
    for (const row of rows) {
      const open = viewingId === row.id;
      const li = el("li", `kb-task-item${row.enabled ? "" : " is-off"}${open ? " is-open" : ""}`);
      const head = el("button", "kb-task-head") as HTMLButtonElement;
      head.type = "button";
      head.title = "查看";
      head.append(
        el("strong", "", row.title || row.id),
        el("span", "kb-agent-chip", modeZh(row.mode)),
        el("span", "muted", row.enabled ? "开" : "关"),
      );
      head.onclick = () => void openView(row.id);
      const meta = el("p", "muted kb-task-meta");
      const bits = [preview(row.message)];
      if (row.mode === "schedule") bits.push(scheduleZh(row), row.next_run ? `下次 ${clock(row.next_run)}` : "");
      if (row.mode === "trigger") {
        bits.push(triggerZh(row.trigger, catalog));
        if ((row.condition || "").trim()) bits.push(`条件 ${preview(row.condition || "", 24)}`);
      }
      if (row.builtin) bits.push("内置");
      meta.textContent = bits.filter(Boolean).join(" · ");
      const bar = el("div", "kb-agent-actions");
      const view = el("button", "btn btn-secondary", open ? "收起" : "查看") as HTMLButtonElement;
      const edit = el("button", "btn btn-secondary", "编辑") as HTMLButtonElement;
      const run = el("button", "btn btn-secondary", "跑一次") as HTMLButtonElement;
      const tog = el("button", "btn btn-secondary", row.enabled ? "停用" : "启用") as HTMLButtonElement;
      view.type = "button";
      edit.type = "button";
      run.type = "button";
      tog.type = "button";
      view.onclick = () => void openView(row.id);
      edit.onclick = () => {
        if (viewingId !== row.id) void openView(row.id);
        setEdit(row.id);
      };
      run.onclick = async () => {
        const extra: { book_id?: string } = {};
        if ((row.message || "").includes("{{book_id}}")) {
          const bookId = (window.prompt("书号 book_id（会填进对话里的 {{book_id}}）", "") || "").trim();
          if (!bookId) return;
          extra.book_id = bookId;
        }
        try {
          await agent.runTask(row.id, extra);
          await refresh();
        } catch (e) {
          window.alert((e as Error).message || "启动失败");
        }
      };
      tog.onclick = async () => {
        if (row.enabled) await agent.cancelTask(row.id);
        else await agent.patchTask(row.id, { enabled: true });
        await refresh();
      };
      bar.append(view, edit, run, tog);
      li.append(head, meta, bar);
      if (open) paintView(li, row);
      list.append(li);
    }
  }

  modeSel.onchange = syncWhen;
  repeatSel.onchange = syncWhen;

  cancelEdit.onclick = () => {
    setEdit("");
    fillForm(null);
  };

  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const mode = modeSel.value;
    const message = messageIn.value.trim();
    if (!message) {
      window.alert("请填写发给 Agent 的对话");
      return;
    }
    const body: {
      title: string;
      mode: string;
      cron: string;
      next_run: string;
      trigger: string;
      condition: string;
      message: string;
    } = {
      title: titleIn.value.trim() || "任务",
      mode,
      cron: "",
      next_run: "",
      trigger: "",
      condition: "",
      message,
    };
    try {
      if (mode === "schedule") {
        const when = buildSchedule();
        body.cron = when.cron;
        body.next_run = when.next_run;
      } else {
        body.trigger = triggerSel.value || IMPORT_DONE;
        body.condition = conditionIn.value.trim();
      }
      if (editingId) await agent.patchTask(editingId, body);
      else await agent.createTask({ ...body, enabled: true });
      await loadEvents();
      setEdit("");
      fillForm(null);
      await refresh();
    } catch (e) {
      window.alert((e as Error).message || (editingId ? "保存失败" : "新建失败"));
    }
  };

  async function loadEvents(): Promise<void> {
    try {
      catalog = (await agent.taskEvents()).events || [];
    } catch {
      catalog = [{ id: IMPORT_DONE, label: "导入教材完成后", group: "系统" }];
    }
    fillTriggerSelect(triggerSel, catalog, triggerSel.value || IMPORT_DONE);
  }

  void loadEvents().then(() => {
    fillForm(null);
    void refresh();
  });
  stopLive = onLive((ev: LiveEvent) => {
    if (ev.type === "task" || (isJob(ev) && (ev.kind === "page_refine" || ev.kind === "task"))) {
      void refresh();
    }
  });

  return {
    destroy() {
      stopLive?.();
      stopLive = null;
    },
  };
}
