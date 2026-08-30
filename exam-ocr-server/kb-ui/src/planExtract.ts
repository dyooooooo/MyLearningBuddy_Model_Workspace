export type TraceLike = { title?: string; kind?: string; detail?: string };

export function isCreatePlanTitle(title: string): boolean {
  return /createplan/i.test(title || "");
}

export function looksLikePlan(text: string): boolean {
  const raw = (text || "").trim();
  if (!raw) return false;
  if (raw.startsWith("#")) return true;
  if (raw.length < 40) return false;
  if (/^\s*[-*]\s+\S/m.test(raw) && raw.includes("\n")) return true;
  return /^\s*\d+\.\s+\S/m.test(raw);
}

export function titleFromMarkdown(text: string): string {
  const raw = (text || "").trim();
  const m = raw.match(/^#{1,3}\s+(.+)$/m);
  if (m) return m[1].trim().slice(0, 80);
  return raw.split("\n")[0]?.replace(/^#+\s*/, "").trim().slice(0, 80) || "执行计划";
}

export function extractPlanMarkdown(steps: TraceLike[]): string {
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i];
    if (!isCreatePlanTitle(String(step.title || ""))) continue;
    const md = markdownFromCreatePlan(String(step.detail || ""));
    if (md) return md;
  }
  return "";
}

export function markdownFromCreatePlan(detail: string): string {
  const raw = (detail || "").trim();
  if (!raw) return "";
  try {
    const obj = JSON.parse(raw) as { plan?: unknown; markdown?: unknown; content?: unknown };
    const plan = obj.plan ?? obj.markdown ?? obj.content;
    if (typeof plan === "string" && plan.trim()) return plan.trim();
  } catch {
    /* truncated JSON from older ingest */
  }
  return planFieldPrefix(raw);
}

function planFieldPrefix(text: string): string {
  const m = text.match(/"plan"\s*:\s*"/);
  if (!m || m.index == null) {
    return text.startsWith("#") && text.length > 40 ? text : "";
  }
  return unescapeJsonStringPrefix(text.slice(m.index + m[0].length));
}

function unescapeJsonStringPrefix(s: string): string {
  let out = "";
  for (let i = 0; i < s.length; i += 1) {
    const ch = s[i];
    if (ch === "\\" && i + 1 < s.length) {
      const nxt = s[i + 1];
      if (nxt === "u" && i + 5 < s.length) {
        const code = Number.parseInt(s.slice(i + 2, i + 6), 16);
        if (!Number.isNaN(code)) {
          out += String.fromCharCode(code);
          i += 5;
          continue;
        }
      }
      const map: Record<string, string> = { n: "\n", t: "\t", r: "\r", '"': '"', "\\": "\\", "/": "/" };
      out += map[nxt] ?? nxt;
      i += 1;
      continue;
    }
    if (ch === '"') break;
    out += ch;
  }
  return out.trim();
}
