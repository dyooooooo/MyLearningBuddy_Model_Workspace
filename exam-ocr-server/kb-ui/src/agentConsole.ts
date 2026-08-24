import {
  agent,
  type AgentCheckpoint,
  type AgentConfig,
  type AgentHookEntry,
  type AgentJob,
} from "./api";
import { renderAgentTurn } from "./agentTurn";
import { hardReload, isJob, onLive, type LiveEvent } from "./live";
import { el } from "./util/dom";

type View = "live" | "config";

export function mountAgentConsole(host: HTMLElement): {
  destroy: () => void;
  setView: (next: View) => void;
} {
  host.className = "kb-agent-host kb-agent";
  const bar = el("header", "kb-agent-bar");
  const title = el("div", "kb-agent-titlewrap");
  const heading = el("h2", "kb-agent-title", "过往轨迹");
  const sub = el("p", "muted kb-agent-sub", "和右侧一样：一次对话一条记录，点开看全部回合。");
  title.append(heading, sub);
  const filesHint = el("p", "kb-agent-files");
  const liveDot = el("span", "kb-agent-dot", "实时");
  const msg = el("p", "kb-msg muted");
  bar.append(title, filesHint, liveDot);
  host.append(bar, msg);

  const live = el("div", "kb-agent-live");
  const jobCol = el("aside", "kb-agent-jobscol");
  jobCol.append(el("h3", "kb-agent-h", "对话"));
  const jobList = el("ul", "kb-agent-jobs");
  jobCol.append(jobList);
  const detail = el("section", "kb-agent-detailpane");
  detail.append(el("p", "muted kb-agent-empty", "左边点一条，按对话回合回放。"));
  live.append(jobCol, detail);

  const config = el("div", "kb-agent-config hidden");
  const reloadBtn = el("button", "btn btn-primary", "重载") as HTMLButtonElement;
  reloadBtn.type = "button";
  const cfgIntro = el("p", "muted", "改工作区里的规矩：技能、规则、提示词、钩子、插件和小工具。不影响书库导入。");
  const cfgHead = el("div", "kb-agent-cfghead");
  cfgHead.append(el("h3", "kb-agent-h", "设置"), reloadBtn);
  const cfgTabs = el("div", "kb-settings-tabs");
  const cfgPane = el("div", "kb-settings-pane");
  type CfgTab = "skills" | "rules" | "tools" | "mcp" | "prompts" | "hooks" | "plugins" | "checkpoints";
  let cfgTab: CfgTab = "skills";
  const tabBtns = new Map<CfgTab, HTMLButtonElement>();
  for (const [id, label] of [
    ["skills", "技能"],
    ["rules", "规则"],
    ["tools", "工具"],
    ["mcp", "外接"],
    ["prompts", "提示词"],
    ["hooks", "钩子"],
    ["plugins", "插件"],
    ["checkpoints", "检查点"],
  ] as const) {
    const btn = el("button", "kb-settings-tab", label) as HTMLButtonElement;
    btn.type = "button";
    btn.onclick = () => {
      cfgTab = id;
      paintCfgTabs();
      paintCfgPane();
    };
    tabBtns.set(id, btn);
    cfgTabs.append(btn);
  }
  config.append(cfgHead, cfgIntro, cfgTabs, cfgPane);

  host.append(live, config);

  function paintCfgTabs(): void {
    tabBtns.forEach((btn, id) => btn.classList.toggle("is-on", id === cfgTab));
  }

  function paintCfgPane(): void {
    if (!cfg && cfgTab !== "checkpoints") return;
    if (cfgTab === "mcp") renderMcp(cfgPane);
    else if (cfgTab === "tools") renderTools(cfgPane);
    else if (cfgTab === "prompts") renderPrompts(cfgPane);
    else if (cfgTab === "hooks") renderHooks(cfgPane);
    else if (cfgTab === "plugins") renderPlugins(cfgPane);
    else if (cfgTab === "checkpoints") void renderCheckpoints(cfgPane);
    else renderDocs(cfgPane, cfgTab === "skills" ? cfg?.skills || [] : cfg?.rules || [], cfgTab === "skills" ? "skill" : "rule");
  }

  type ConvRow = {
    id: string;
    title: string;
    updated_at: string;
    status: string;
    turns: number;
    preview: string;
  };

  let cfg: AgentConfig | null = null;
  let convs: ConvRow[] = [];
  let grades: AgentJob[] = [];
  let activeConv = "";
  let activeGrade = "";
  let stopLive: (() => void) | null = null;

  function setMsg(text: string, err = false): void {
    msg.textContent = text;
    msg.classList.toggle("is-error", err);
  }

  function setView(next: View): void {
    live.classList.toggle("hidden", next !== "live");
    config.classList.toggle("hidden", next !== "config");
    liveDot.classList.toggle("hidden", next !== "live");
    heading.textContent = next === "live" ? "过往轨迹" : "管理配置";
    sub.textContent =
      next === "live"
        ? "一次对话一条。点开回放全部回合。"
        : "技能、规则、工具、提示词、钩子、插件、检查点。";
  }

  async function refreshConfig(): Promise<void> {
    cfg = await agent.config();
    paintCfgTabs();
    paintCfgPane();
  }

  async function refreshJobs(): Promise<void> {
    const [cOut, jOut] = await Promise.all([agent.conversations(), agent.jobs()]);
    convs = cOut.conversations || [];
    grades = (jOut.jobs || []).filter((j) => j.kind === "grade");
    if (!activeConv && !activeGrade) {
      if (convs[0]) activeConv = convs[0].id;
      else if (grades[0]) activeGrade = grades[0].job_id;
    }
    renderJobs();
    if (activeConv) await renderConv(activeConv);
    else {
      const row = grades.find((j) => j.job_id === activeGrade);
      if (row) renderGrade(row);
    }
  }

  function renderMcp(hostEl: HTMLElement): void {
    const servers = cfg?.mcp?.mcpServers || {};
    hostEl.replaceChildren();
    hostEl.append(el("p", "muted kb-settings-hint", "外接能力（MCP）：接文件系统、网页接口等现成服务。"));
    const names = Object.keys(servers);
    if (!names.length) hostEl.append(el("p", "muted", "还没有外接服务。"));
    for (const name of names) {
      const spec = servers[name] || {};
      const row = el("div", "kb-agent-row");
      const kind = spec.url ? "网页接口" : "本地命令";
      const meta = spec.url ? String(spec.url) : [spec.command, ...((spec.args as string[]) || [])].join(" ");
      row.append(el("strong", "", name), el("span", "kb-agent-chip", kind), el("span", "muted", meta));
      const del = el("button", "btn btn-secondary", "删除") as HTMLButtonElement;
      del.type = "button";
      del.onclick = async () => {
        const next = { ...servers };
        delete next[name];
        await agent.putMcp(next);
        await agent.reload();
        await refreshConfig();
        setMsg("已删除外接服务");
      };
      row.append(del);
      hostEl.append(row);
    }
    const form = el("div", "kb-agent-form");
    const nameIn = input("名称", "例如 filesystem");
    const kind = select("接法", [
      ["http", "网页接口（填网址）"],
      ["stdio", "本地命令（填程序名）"],
    ]);
    const urlIn = input("网址或程序", "https://… 或 npx");
    const extraIn = input("附加", "网页：headers 的 JSON；本地：参数，空格分开");
    const add = el("button", "btn btn-secondary", "添加") as HTMLButtonElement;
    add.type = "button";
    add.onclick = async () => {
      const name = nameIn.field.value.trim();
      if (!name) return;
      const raw = urlIn.field.value.trim();
      const extra = extraIn.field.value.trim();
      const next = { ...(cfg?.mcp?.mcpServers || {}) };
      if (kind.field.value === "http") {
        let headers: Record<string, string> | undefined;
        if (extra) {
          try {
            headers = JSON.parse(extra) as Record<string, string>;
          } catch {
            setMsg("附加字段必须是 JSON", true);
            return;
          }
        }
        next[name] = { url: raw, ...(headers ? { headers } : {}) };
      } else {
        next[name] = { command: raw, args: extra ? extra.split(/\s+/) : [] };
      }
      await agent.putMcp(next);
      await agent.reload();
      await refreshConfig();
      setMsg("外接服务已保存");
    };
    form.append(nameIn.wrap, kind.wrap, urlIn.wrap, extraIn.wrap, add);
    hostEl.append(form);
  }

  function renderDocs(
    hostEl: HTMLElement,
    items: { id: string; content: string }[],
    kind: "skill" | "rule",
  ): void {
    hostEl.replaceChildren();
    const noun = kind === "skill" ? "技能" : "规则";
    hostEl.append(
      el(
        "p",
        "muted kb-settings-hint",
        kind === "skill" ? "技能：教它按什么步骤找书、判题。" : "规则：硬约束，例如只用教材、禁止通识。",
      ),
    );
    if (!items.length) hostEl.append(el("p", "muted", `还没有${noun}。`));
    for (const item of items) {
      const row = el("details", "kb-agent-edit");
      row.append(el("summary", "", item.id));
      const ta = document.createElement("textarea");
      ta.className = "kb-agent-ta";
      ta.value = item.content;
      const bar = el("div", "kb-agent-actions");
      const save = el("button", "btn btn-secondary", "保存") as HTMLButtonElement;
      const del = el("button", "btn btn-secondary", "删除") as HTMLButtonElement;
      save.type = "button";
      del.type = "button";
      save.onclick = async () => {
        if (kind === "skill") await agent.putSkill(item.id, ta.value);
        else await agent.putRule(item.id, ta.value);
        await agent.reload();
        await refreshConfig();
        setMsg(`${noun}「${item.id}」已保存`);
      };
      del.onclick = async () => {
        if (!window.confirm(`删除${noun}「${item.id}」？`)) return;
        if (kind === "skill") await agent.deleteSkill(item.id);
        else await agent.deleteRule(item.id);
        await agent.reload();
        await refreshConfig();
      };
      bar.append(save, del);
      row.append(ta, bar);
      hostEl.append(row);
    }
    const form = el("div", "kb-agent-form");
    const idIn = input(`新${noun}编号`, kind === "skill" ? "search-files" : "extra");
    const add = el("button", "btn btn-secondary", `添加${noun}`) as HTMLButtonElement;
    add.type = "button";
    add.onclick = async () => {
      const id = idIn.field.value.trim();
      if (!id) return;
      const stub =
        kind === "skill"
          ? `---\nname: ${id}\ndescription: \n---\n\n# ${id}\n`
          : `---\ndescription: ${id}\nalwaysApply: false\n---\n\n# ${id}\n`;
      if (kind === "skill") await agent.putSkill(id, stub);
      else await agent.putRule(id, stub);
      await agent.reload();
      await refreshConfig();
    };
    form.append(idIn.wrap, add);
    hostEl.append(form);
  }

  function renderTools(hostEl: HTMLElement): void {
    const items = cfg?.tools?.tools || [];
    hostEl.replaceChildren();
    hostEl.append(el("p", "muted kb-settings-hint", "工具：本机小函数，例如搜 Markdown、打开 PDF 某页。"));
    if (!items.length) hostEl.append(el("p", "muted", "还没有工具。"));
    for (const item of items) {
      const row = el("details", "kb-agent-edit");
      row.append(el("summary", "", `${item.id}  ·  ${item.path || ""}`));
      const desc = document.createElement("input");
      desc.className = "kb-agent-input";
      desc.placeholder = "这工具做什么";
      desc.value = item.description || "";
      const ta = document.createElement("textarea");
      ta.className = "kb-agent-ta";
      ta.value = item.content || "";
      const bar = el("div", "kb-agent-actions");
      const save = el("button", "btn btn-secondary", "保存") as HTMLButtonElement;
      const del = el("button", "btn btn-secondary", "删除") as HTMLButtonElement;
      save.type = "button";
      del.type = "button";
      save.onclick = async () => {
        await agent.putTool(item.id, { path: item.path, description: desc.value, content: ta.value });
        await agent.reload();
        await refreshConfig();
        setMsg(`工具「${item.id}」已保存`);
      };
      del.onclick = async () => {
        if (!window.confirm(`从清单移除「${item.id}」？`)) return;
        await agent.deleteTool(item.id);
        await agent.reload();
        await refreshConfig();
      };
      bar.append(save, del);
      row.append(desc, ta, bar);
      hostEl.append(row);
    }
    const form = el("div", "kb-agent-form");
    const idIn = input("新工具编号", "search_md");
    const pathIn = input("文件名", "search_md.py");
    const add = el("button", "btn btn-secondary", "添加工具") as HTMLButtonElement;
    add.type = "button";
    add.onclick = async () => {
      const id = idIn.field.value.trim();
      const path = pathIn.field.value.trim() || `${id}.py`;
      if (!id) return;
      await agent.putTool(id, { path, description: id, content: `def ${id}():\n    return {}\n` });
      await agent.reload();
      await refreshConfig();
    };
    form.append(idIn.wrap, pathIn.wrap, add);
    hostEl.append(form);
  }

  function renderPrompts(hostEl: HTMLElement): void {
    const items = cfg?.prompts || [];
    hostEl.replaceChildren();
    hostEl.append(
      el("p", "muted kb-settings-hint", "系统提示词：改的是 Agent 自己的说明书。清空或写坏会自动回落到镜像种子。起标题那一枪永远用种子。"),
    );
    if (!items.length) hostEl.append(el("p", "muted", "还没有提示词。"));
    for (const item of items) {
      const row = el("details", "kb-agent-edit");
      row.append(el("summary", "", `${item.id}  ·  ${item.source === "workspace" ? "工作区" : "种子"}`));
      const ta = document.createElement("textarea");
      ta.className = "kb-agent-ta";
      ta.value = item.content;
      const bar = el("div", "kb-agent-actions");
      const save = el("button", "btn btn-secondary", "保存") as HTMLButtonElement;
      const reset = el("button", "btn btn-secondary", "恢复出厂") as HTMLButtonElement;
      save.type = "button";
      reset.type = "button";
      save.onclick = async () => {
        await agent.putPrompt(item.id, ta.value);
        await refreshConfig();
        setMsg(`提示词「${item.id}」已保存`);
      };
      reset.onclick = async () => {
        if (!window.confirm(`把「${item.id}」恢复成镜像种子？`)) return;
        await agent.deletePrompt(item.id);
        await refreshConfig();
        setMsg(`提示词「${item.id}」已恢复出厂`);
      };
      bar.append(save, reset);
      row.append(ta, bar);
      hostEl.append(row);
    }
  }

  function renderHooks(hostEl: HTMLElement): void {
    const pack = cfg?.hooks || {};
    const hooks = pack.hooks || {};
    const scripts = pack.scripts || [];
    hostEl.replaceChildren();
    hostEl.append(
      el(
        "p",
        "muted kb-settings-hint",
        "钩子：Cursor 官方引擎读 .cursor/hooks.json。安全类请开 failClosed。外链文档：cursor.com/docs/hooks",
      ),
    );
    const events = Object.keys(hooks);
    if (!events.length) hostEl.append(el("p", "muted", "还没有登记钩子事件。"));
    for (const event of events) {
      const block = el("div", "kb-agent-block");
      block.append(el("strong", "", event));
      const rows = hooks[event] || [];
      rows.forEach((entry, idx) => {
        const line = el("div", "kb-agent-row");
        const cmd = document.createElement("input");
        cmd.className = "kb-agent-input";
        cmd.value = String(entry.command || entry.prompt || "");
        const closed = document.createElement("input");
        closed.type = "checkbox";
        closed.checked = Boolean(entry.failClosed);
        const save = el("button", "btn btn-secondary", "保存") as HTMLButtonElement;
        const del = el("button", "btn btn-secondary", "删除") as HTMLButtonElement;
        save.type = "button";
        del.type = "button";
        save.onclick = async () => {
          const next = structuredClone(hooks);
          const cur = { ...(next[event][idx] || {}) };
          if ((cur.type || "command") === "prompt") cur.prompt = cmd.value;
          else cur.command = cmd.value;
          cur.failClosed = closed.checked;
          next[event][idx] = cur;
          await agent.putHooks({ version: 1, hooks: next });
          await refreshConfig();
        };
        del.onclick = async () => {
          const next = structuredClone(hooks);
          next[event] = (next[event] || []).filter((_, i) => i !== idx);
          if (!next[event].length) delete next[event];
          await agent.putHooks({ version: 1, hooks: next });
          await refreshConfig();
        };
        line.append(cmd, el("label", "muted", "failClosed"), closed, save, del);
        block.append(line);
      });
      hostEl.append(block);
    }
    const form = el("div", "kb-agent-form");
    const evIn = input("事件", "beforeShellExecution");
    const cmdIn = input("命令", ".cursor/hooks/audit-shell.sh");
    const add = el("button", "btn btn-secondary", "添加钩子") as HTMLButtonElement;
    add.type = "button";
    add.onclick = async () => {
      const event = evIn.field.value.trim();
      const command = cmdIn.field.value.trim();
      if (!event || !command) return;
      const next: Record<string, AgentHookEntry[]> = { ...hooks };
      next[event] = [...(next[event] || []), { type: "command", command, failClosed: true }];
      await agent.putHooks({ version: 1, hooks: next });
      await refreshConfig();
    };
    form.append(evIn.wrap, cmdIn.wrap, add);
    hostEl.append(form);
    hostEl.append(el("h3", "kb-agent-h", "脚本"));
    for (const item of scripts) {
      const row = el("details", "kb-agent-edit");
      row.append(el("summary", "", item.id));
      const ta = document.createElement("textarea");
      ta.className = "kb-agent-ta";
      ta.value = item.content;
      const bar = el("div", "kb-agent-actions");
      const save = el("button", "btn btn-secondary", "保存") as HTMLButtonElement;
      const del = el("button", "btn btn-secondary", "删除") as HTMLButtonElement;
      save.type = "button";
      del.type = "button";
      save.onclick = async () => {
        await agent.putHookScript(item.id, ta.value);
        await refreshConfig();
      };
      del.onclick = async () => {
        if (!window.confirm(`删除脚本「${item.id}」？`)) return;
        await agent.deleteHookScript(item.id);
        await refreshConfig();
      };
      bar.append(save, del);
      row.append(ta, bar);
      hostEl.append(row);
    }
    const scriptForm = el("div", "kb-agent-form");
    const nameIn = input("新脚本名", "audit-shell.sh");
    const addScript = el("button", "btn btn-secondary", "添加脚本") as HTMLButtonElement;
    addScript.type = "button";
    addScript.onclick = async () => {
      const name = nameIn.field.value.trim();
      if (!name) return;
      await agent.putHookScript(name, "#!/bin/bash\nprintf '%s\\n' '{\"permission\":\"allow\"}'\n");
      await refreshConfig();
    };
    scriptForm.append(nameIn.wrap, addScript);
    hostEl.append(scriptForm);
  }

  function renderPlugins(hostEl: HTMLElement): void {
    const items = cfg?.plugins || [];
    hostEl.replaceChildren();
    hostEl.append(
      el(
        "p",
        "muted kb-settings-hint",
        "插件：装到 .cursor/plugins/local/。市场页面借不了，只支持本地 zip 与白名单 Git。货架：cursor.com/marketplace · cursor.directory",
      ),
    );
    const allow = (cfg?.plugin_allowlist || []).join(" ") || "（空：Git 安装会被拒）";
    hostEl.append(el("p", "muted", `Git 白名单前缀：${allow}`));
    if (!items.length) hostEl.append(el("p", "muted", "还没有插件。"));
    for (const item of items) {
      const row = el("div", "kb-agent-row");
      row.append(
        el("strong", "", item.id),
        el("span", "kb-agent-chip", item.enabled ? "启用" : "停用"),
        el("span", "muted", `${item.version || "—"} · ${(item.parts || []).join("/") || "空"}`),
      );
      const tog = el("button", "btn btn-secondary", item.enabled ? "停用" : "启用") as HTMLButtonElement;
      const del = el("button", "btn btn-secondary", "删除") as HTMLButtonElement;
      tog.type = "button";
      del.type = "button";
      tog.onclick = async () => {
        await agent.setPluginEnabled(item.id, !item.enabled);
        await refreshConfig();
      };
      del.onclick = async () => {
        if (!window.confirm(`删除插件「${item.id}」？`)) return;
        await agent.deletePlugin(item.id);
        await refreshConfig();
      };
      row.append(tog, del);
      hostEl.append(row);
    }
    const form = el("div", "kb-agent-form");
    const file = document.createElement("input");
    file.type = "file";
    file.accept = ".zip";
    const up = el("button", "btn btn-secondary", "上传 zip") as HTMLButtonElement;
    up.type = "button";
    up.onclick = async () => {
      const picked = file.files?.[0];
      if (!picked) return;
      try {
        await agent.uploadPlugin(picked);
        await refreshConfig();
        setMsg(`已安装 ${picked.name}`);
      } catch (e) {
        setMsg((e as Error).message || "安装失败", true);
      }
    };
    const urlIn = input("Git 地址", "https://github.com/org/plugin");
    const refIn = input("分支/标签", "可空");
    const git = el("button", "btn btn-secondary", "从 Git 安装") as HTMLButtonElement;
    git.type = "button";
    git.onclick = async () => {
      const url = urlIn.field.value.trim();
      if (!url) return;
      try {
        await agent.installPluginGit(url, refIn.field.value.trim());
        await refreshConfig();
        setMsg("插件已安装");
      } catch (e) {
        setMsg((e as Error).message || "不在白名单或克隆失败", true);
      }
    };
    form.append(file, up, urlIn.wrap, refIn.wrap, git);
    hostEl.append(form);
  }

  async function renderCheckpoints(hostEl: HTMLElement): Promise<void> {
    hostEl.replaceChildren(el("p", "muted", "读取检查点…"));
    let rows: AgentCheckpoint[] = [];
    try {
      rows = (await agent.checkpoints(80)).checkpoints || [];
    } catch (e) {
      hostEl.replaceChildren(el("p", "kb-msg is-error", (e as Error).message || "读检查点失败"));
      return;
    }
    hostEl.replaceChildren();
    hostEl.append(el("p", "muted kb-settings-hint", "每轮对话开始前自动打点。恢复默认不动聊天记录。"));
    const snapBtn = el("button", "btn btn-secondary", "现在打一个点") as HTMLButtonElement;
    snapBtn.type = "button";
    snapBtn.onclick = async () => {
      await agent.createCheckpoint("手动");
      await renderCheckpoints(hostEl);
    };
    hostEl.append(snapBtn);
    if (!rows.length) {
      hostEl.append(el("p", "muted", "还没有检查点。"));
      return;
    }
    for (const row of rows) {
      const box = el("div", "kb-agent-block");
      box.append(
        el("strong", "", row.note || row.kind),
        el("span", "kb-agent-chip", row.kind),
        el("span", "muted", `${row.time || ""} · ${row.file_count ?? (row.files || []).length} 个文件 · ${row.id.slice(0, 12)}`),
      );
      const bar = el("div", "kb-agent-actions");
      const diffBtn = el("button", "btn btn-secondary", "看 diff") as HTMLButtonElement;
      const restBtn = el("button", "btn btn-secondary", "恢复") as HTMLButtonElement;
      const forkBtn = el("button", "btn btn-secondary", "分叉") as HTMLButtonElement;
      diffBtn.type = "button";
      restBtn.type = "button";
      forkBtn.type = "button";
      const pre = el("pre", "kb-agent-ta");
      pre.style.display = "none";
      diffBtn.onclick = async () => {
        const out = await agent.checkpointDiff(row.id);
        pre.textContent = out.text || (out.files || []).join("\n") || "（无）";
        pre.style.display = "";
      };
      restBtn.onclick = async () => {
        if (!window.confirm("回到这个检查点？聊天记录会留下。")) return;
        await agent.restoreCheckpoint(row.id);
        hardReload();
      };
      forkBtn.onclick = async () => {
        const name = window.prompt("分叉名称", row.kind || "try") || "";
        if (!name.trim()) return;
        const out = await agent.forkCheckpoint(row.id, name.trim());
        window.dispatchEvent(
          new CustomEvent("kb:open-conversation", { detail: { id: out.conversation_id, title: out.title } }),
        );
        hardReload();
      };
      bar.append(diffBtn, restBtn, forkBtn);
      box.append(bar, pre);
      hostEl.append(box);
    }
  }

  function renderJobs(): void {
    jobList.replaceChildren();
    if (!convs.length && !grades.length) {
      jobList.append(el("li", "muted", "还没有对话。右侧聊过会出现在这里。"));
      return;
    }
    for (const conv of convs) {
      const li = el("li", `kb-agent-job${conv.id === activeConv ? " is-active" : ""}`);
      const btn = el("button", "kb-agent-jobbtn") as HTMLButtonElement;
      btn.type = "button";
      btn.append(
        badge(statusZh(conv.status), `is-${conv.status}`),
        el("span", "kb-agent-jobkind", conv.turns > 1 ? `${conv.turns} 回合` : "对话"),
        el("span", "kb-agent-jobq", conv.title || conv.preview || "新对话"),
        el("span", "muted kb-agent-jobt", clock(conv.updated_at)),
      );
      btn.onclick = () => {
        activeConv = conv.id;
        activeGrade = "";
        renderJobs();
        void renderConv(conv.id);
        window.dispatchEvent(
          new CustomEvent("kb:open-conversation", { detail: { id: conv.id, title: conv.title } }),
        );
      };
      li.append(btn);
      jobList.append(li);
    }
    for (const job of grades) {
      const li = el("li", `kb-agent-job${job.job_id === activeGrade ? " is-active" : ""}`);
      const btn = el("button", "kb-agent-jobbtn") as HTMLButtonElement;
      btn.type = "button";
      btn.append(
        badge(statusZh(job.status), `is-${job.status}`),
        el("span", "kb-agent-jobkind", "判题"),
        el("span", "kb-agent-jobq", questionOf(job) || "（无摘要）"),
        el("span", "muted kb-agent-jobt", clock(job.updated_at)),
      );
      btn.onclick = () => {
        activeGrade = job.job_id;
        activeConv = "";
        renderJobs();
        renderGrade(job);
      };
      li.append(btn);
      jobList.append(li);
    }
  }

  async function renderConv(id: string): Promise<void> {
    let thread: { conversation: { title?: string }; jobs: AgentJob[] };
    try {
      thread = await agent.conversation(id);
    } catch {
      detail.replaceChildren(el("p", "muted", "这条对话读不出来。"));
      return;
    }
    const title = thread.conversation?.title || "新对话";
    const turns = thread.jobs || [];
    detail.replaceChildren();
    const head = el("header", "kb-agent-case");
    head.append(el("strong", "", title), el("span", "muted", `${turns.length} 回合`));
    detail.append(head);
    if (!turns.length) {
      detail.append(el("p", "muted kb-agent-empty", "还没有回合。"));
      return;
    }
    for (const job of turns) {
      const turn = el("article", "kb-turn kb-turn--replay");
      turn.dataset.jobId = job.job_id;
      renderAgentTurn(turn, job, true);
      detail.append(turn);
    }
  }

  function renderGrade(job: AgentJob): void {
    detail.replaceChildren();
    const head = el("header", "kb-agent-case");
    head.append(
      badge(statusZh(job.status), `is-${job.status}`),
      el("strong", "", "判题"),
      el("span", "muted", clock(job.updated_at)),
    );
    const turn = el("article", "kb-turn kb-turn--replay");
    turn.dataset.jobId = job.job_id;
    renderAgentTurn(turn, job, true);
    detail.append(head, turn);
  }

  let listTimer = 0;
  function scheduleListRefresh(): void {
    if (listTimer) return;
    listTimer = window.setTimeout(() => {
      listTimer = 0;
      void refreshJobs();
    }, 800);
  }

  function onEvent(ev: LiveEvent): void {
    if (ev.type && ev.type !== "job") {
      if (ev.type === "reload" || (ev.type === "checkpoint" && (ev.action === "restore" || ev.action === "fork"))) {
        hardReload();
        return;
      }
      if (["config", "hooks", "plugins", "prompts"].includes(String(ev.type))) {
        void refreshConfig();
      }
      return;
    }
    try {
      if (!isJob(ev)) return;
      const row = ev as unknown as AgentJob;
      if (row.kind === "grade") {
        const idx = grades.findIndex((j) => j.job_id === row.job_id);
        if (idx >= 0) grades[idx] = row;
        else grades.unshift(row);
        renderJobs();
        if (row.job_id === activeGrade) renderGrade(row);
        return;
      }
      const cid = String(row.payload?.conversation_id || row.result?.conversation_id || "");
      if (cid && cid === activeConv) {
        const turn = detail.querySelector<HTMLElement>(`[data-job-id="${cssJob(row.job_id)}"]`);
        if (turn) {
          renderAgentTurn(turn, row, true);
          return;
        }
      }
      scheduleListRefresh();
    } catch {
      /* ignore keepalive */
    }
  }

  reloadBtn.onclick = async () => {
    await agent.reload();
    await refreshConfig();
    setMsg("配置已重载，下一道题生效");
  };

  void (async () => {
    try {
      await refreshConfig();
      await refreshJobs();
      const files = await agent.books();
      const n = files.files?.length || 0;
      filesHint.textContent = n ? `工作区教材 ${n} 个文件` : "工作区还没有 PDF / Markdown";
      filesHint.classList.toggle("is-warn", n === 0);
    } catch (e) {
      setMsg((e as Error).message || "加载失败", true);
    }
    try {
      stopLive = onLive(onEvent);
      liveDot.classList.add("is-on");
    } catch {
      liveDot.textContent = "未连接";
    }
  })();

  return {
    setView,
    destroy() {
      stopLive?.();
      stopLive = null;
      if (listTimer) window.clearTimeout(listTimer);
    },
  };
}

function input(label: string, placeholder: string): { wrap: HTMLElement; field: HTMLInputElement } {
  const wrap = el("label", "kb-field");
  wrap.append(el("span", "kb-field-label", label));
  const field = document.createElement("input");
  field.className = "kb-agent-input";
  field.placeholder = placeholder;
  wrap.append(field);
  return { wrap, field };
}

function select(label: string, options: [string, string][]): { wrap: HTMLElement; field: HTMLSelectElement } {
  const wrap = el("label", "kb-field");
  wrap.append(el("span", "kb-field-label", label));
  const field = document.createElement("select");
  field.className = "kb-agent-input";
  for (const [id, text] of options) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = text;
    field.append(opt);
  }
  wrap.append(field);
  return { wrap, field };
}

function badge(text: string, extra: string): HTMLElement {
  return el("span", `kb-agent-badge ${extra}`, text);
}

function questionOf(job: AgentJob): string {
  const p = job.payload || {};
  return String(p.problem_text || p.message || "").replace(/\s+/g, " ").trim().slice(0, 42);
}

function clock(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 19);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function pad(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function statusZh(s: string): string {
  if (s === "queued") return "排队";
  if (s === "running") return "进行中";
  if (s === "completed") return "完成";
  if (s === "failed") return "失败";
  return s;
}

function cssJob(id: string): string {
  return id.replace(/["\\]/g, "\\$&");
}
