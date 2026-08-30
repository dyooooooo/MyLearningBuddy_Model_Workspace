export type TraceStep = {
  t?: string;
  kind: string;
  title: string;
  detail: string;
};

export type CollapsedTool = {
  name: string;
  status: string;
  files: string[];
  detail: string;
};

const HEARTBEAT_TITLES = new Set([
  "connect",
  "still running",
  "bridge retry",
  "status",
  "status running",
  "request",
]);

export function isHeartbeatTitle(title: string): boolean {
  const t = title.trim().toLowerCase();
  if (HEARTBEAT_TITLES.has(t)) return true;
  return t.startsWith("still running") || t.startsWith("status ");
}

export function parseTool(step: TraceStep): { name: string; status: string; file: string; detail: string } {
  const title = (step.title || step.kind || "tool").trim();
  const m = title.match(/^(\S+)\s+(running|completed|failed)$/i);
  return {
    name: m ? m[1] : title,
    status: m ? m[2].toLowerCase() : "completed",
    file: extractFile(step.detail),
    detail: step.detail || "",
  };
}

export function extractFile(detail: string): string {
  const raw = (detail || "").trim();
  if (!raw) return "";
  try {
    const obj = JSON.parse(raw) as { path?: string; file?: string };
    const p = obj.path || obj.file || "";
    if (p) return shortPath(p);
  } catch {
    const m = raw.match(/[\w./-]+\.(md|mdc|py|json|yaml|pdf|jpe?g|png|webp)\b/i);
    if (m) return shortPath(m[0]);
  }
  return "";
}

export function shortPath(path: string): string {
  return path.split("/").filter(Boolean).slice(-2).join("/");
}

export function toolHint(detail: string): string {
  const raw = (detail || "").trim();
  if (!raw) return "";
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>;
    const path = String(obj.path || obj.file || obj.target || "").trim();
    if (path) return pageHint(path);
    const toolName = String(obj.toolName || obj.tool_name || "").trim();
    if (toolName) return toolName;
    const query = String(obj.query || obj.pattern || obj.q || obj.keyword || obj.command || "").trim();
    if (query) return query.length > 36 ? `${query.slice(0, 36)}…` : query;
  } catch {
    return pageHint(extractFile(raw) || raw);
  }
  return pageHint(extractFile(raw));
}

export function pageHint(path: string): string {
  const m = path.match(/(?:^|\/)p?0*(\d+)\.(md|jpe?g|png|webp)$/i);
  if (m) return `第 ${Number(m[1])} 页`;
  return shortPath(path);
}

function isImagePath(detail: string): boolean {
  return /\.(jpe?g|png|webp)\b/i.test(detail);
}

export function toolLabel(tool: { name: string; status: string; detail?: string }): string {
  const key = tool.name.toLowerCase();
  const hint = toolHint(tool.detail || "");
  const image = isImagePath(tool.detail || "");
  const map: Record<string, string> = {
    read: image ? "对照原图" : "读取",
    search: "搜索",
    search_md: "搜索教材",
    open_pdf: "打开 PDF",
    open_pdf_page: "打开 PDF",
    list_config: "查看配置",
    write_tool: "写入工具",
    write_skill: "写入技能",
    write_rule: "写入规则",
    reload_config: "重载配置",
    grep: "搜索",
    glob: "列出文件",
    shell: "跑脚本",
    mcp: "调用",
    checkpoint: "打检查点",
    edit: "编辑",
    write: "写入",
    "35b 版式": "35B 版式",
  };
  const base = map[key] || tool.name;
  const label = hint ? `${base} ${hint}` : base;
  return tool.status === "running" ? `${label}…` : label;
}

export function joinThinkFragments(parts: string[]): string[] {
  const out: string[] = [];
  for (const raw of parts) {
    const p = raw.trim();
    if (!p) continue;
    if (!out.length) {
      out.push(p);
      continue;
    }
    const prev = out[out.length - 1];
    if (!/[。！？.!?…]$/.test(prev)) {
      out[out.length - 1] = prev + p;
    } else {
      out.push(p);
    }
  }
  return out;
}

const THINK_TURN = /^(接下来|然后|接着|于是|现在|下一步|已确认|准备)/;

/** 一小步一段：满约 140 字、满 3 句，或出现「接下来/已确认」这类转向时再切开。 */
export function packThinkSegments(sentences: string[], maxChars = 140): string[] {
  const parts = sentences.map((s) => s.trim()).filter(Boolean);
  const out: string[] = [];
  let buf = "";
  let n = 0;
  for (const s of parts) {
    const turn = THINK_TURN.test(s);
    if (buf && (turn || n >= 3 || buf.length + s.length > maxChars)) {
      out.push(buf);
      buf = s;
      n = 1;
    } else {
      buf += s;
      n += 1;
    }
  }
  if (buf) out.push(buf);
  return out;
}

export type TimelineItem =
  | { kind: "think"; text: string }
  | { kind: "tool"; tool: CollapsedTool };

export function lastSentence(text: string): string {
  const t = text.trim().replace(/\s+/g, " ");
  if (!t) return "";
  const parts = t.split(/(?<=[。！？.!?…])/).map((s) => s.trim()).filter(Boolean);
  return (parts[parts.length - 1] || t).slice(0, 80);
}

export function elapsedText(iso?: string, now = Date.now()): string {
  if (!iso) return "";
  const start = Date.parse(iso);
  if (!Number.isFinite(start)) return "";
  const s = Math.max(0, Math.floor((now - start) / 1000));
  if (s < 60) return `${s} 秒`;
  return `${Math.floor(s / 60)} 分 ${s % 60} 秒`;
}

export function liveStatus(input: {
  tools: CollapsedTool[];
  thinks: string[];
  draft: string;
  createdAt?: string;
  now?: number;
}): { headline: string; elapsed: string } {
  const running = [...input.tools].reverse().find((t) => t.status === "running");
  let headline = "";
  if (running) {
    headline = toolLabel({ ...running, status: "completed" });
  } else if (input.thinks.length) {
    headline = lastSentence(input.thinks[input.thinks.length - 1] || "");
  } else if (input.draft.trim()) {
    headline = lastSentence(input.draft);
  }
  headline = headline || "正在处理";
  return { headline, elapsed: elapsedText(input.createdAt, input.now) };
}

export function collapseTrace(steps: TraceStep[]): {
  thinks: string[];
  tools: CollapsedTool[];
  timeline: TimelineItem[];
  draft: string;
} {
  const thinks: string[] = [];
  const tools: CollapsedTool[] = [];
  const timeline: TimelineItem[] = [];
  const burst: string[] = [];
  let draft = "";
  let lastThinkAt = 0;

  const flushThinks = (): void => {
    const packed = packThinkSegments(joinThinkFragments(burst));
    burst.length = 0;
    for (const text of packed) {
      thinks.push(text);
      timeline.push({ kind: "think", text });
    }
  };

  for (const step of steps) {
    if (step.kind === "think") {
      const at = Date.parse(step.t || "") || 0;
      if (burst.length && lastThinkAt && at - lastThinkAt >= 8000) flushThinks();
      if (step.detail) burst.push(step.detail.trim());
      if (at) lastThinkAt = at;
      continue;
    }
    if (step.kind === "answer") {
      if (step.title === "作答中" && step.detail) draft += step.detail;
      continue;
    }
    if (step.kind === "approval") continue;
    if (/createplan/i.test(step.title || "")) continue;
    if (isHeartbeatTitle(step.title || step.kind || "")) continue;
    flushThinks();
    const parsed = parseTool(step);
    const running = [...tools].reverse().find((t) => {
      if (t.name !== parsed.name || t.status !== "running") return false;
      if (parsed.file && t.files.length && !t.files.includes(parsed.file)) return false;
      return true;
    });
    if (running && parsed.status !== "running") {
      running.status = parsed.status;
      if (parsed.file && !running.files.includes(parsed.file)) running.files.push(parsed.file);
      if (parsed.detail) running.detail = parsed.detail;
      continue;
    }
    const tool: CollapsedTool = {
      name: parsed.name,
      status: parsed.status,
      files: parsed.file ? [parsed.file] : [],
      detail: parsed.detail,
    };
    tools.push(tool);
    timeline.push({ kind: "tool", tool });
  }
  flushThinks();
  return { thinks, tools, timeline, draft };
}
