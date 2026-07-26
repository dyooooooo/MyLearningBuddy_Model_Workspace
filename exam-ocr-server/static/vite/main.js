async function c(n="/api/v1/companion"){const e=await fetch(`${n}/graph-runs`);return e.ok?e.json():[]}function i(n){return n.length?n.slice(0,20).map(e=>{var a;return`<article class="list-card agent-run">
        <header><span class="badge">${t(e.status)}</span> ${t(((a=e.started_at)==null?void 0:a.slice(0,19))??e.id.slice(0,8))}</header>
        <ol class="agent-steps">${(e.steps??[]).map(s=>`<li><code>${t(s.node)}</code> ${t(s.detail??"")}</li>`).join("")}</ol>
      </article>`}).join(""):'<p class="placeholder">暂无 Agent 运行记录</p>'}function t(n){return n.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}typeof window<"u"&&(window.BuddyAgentRuns={fetchGraphRuns:c,renderAgentRunsHtml:i});export{c as fetchGraphRuns,i as renderAgentRunsHtml};
