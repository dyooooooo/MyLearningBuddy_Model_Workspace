/**
 * Vite entry — Agent Runs panel helpers (M3).
 * Full companion UI remains in static/companion-ui.js; this bundle adds typed hooks.
 */

export type GraphRunStep = { node: string; ts?: string; detail?: string };

export type GraphRun = {
  id: string;
  homework_id: string;
  status: string;
  steps: GraphRunStep[];
  started_at?: string;
  finished_at?: string;
  error?: string | null;
};

export async function fetchGraphRuns(apiBase = "/api/v1/companion"): Promise<GraphRun[]> {
  const res = await fetch(`${apiBase}/graph-runs`);
  if (!res.ok) return [];
  return res.json();
}

export function renderAgentRunsHtml(runs: GraphRun[]): string {
  if (!runs.length) {
    return '<p class="placeholder">暂无 Agent 运行记录</p>';
  }
  return runs
    .slice(0, 20)
    .map(
      (r) => `<article class="list-card agent-run">
        <header><span class="badge">${escape(r.status)}</span> ${escape(r.started_at?.slice(0, 19) ?? r.id.slice(0, 8))}</header>
        <ol class="agent-steps">${(r.steps ?? [])
          .map((s) => `<li><code>${escape(s.node)}</code> ${escape(s.detail ?? "")}</li>`)
          .join("")}</ol>
      </article>`,
    )
    .join("");
}

function escape(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Expose for optional use from legacy static UI
declare global {
  interface Window {
    BuddyAgentRuns?: { fetchGraphRuns: typeof fetchGraphRuns; renderAgentRunsHtml: typeof renderAgentRunsHtml };
  }
}

if (typeof window !== "undefined") {
  window.BuddyAgentRuns = { fetchGraphRuns, renderAgentRunsHtml };
}
