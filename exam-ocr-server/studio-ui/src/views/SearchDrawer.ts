import { corpus } from "../api/corpus";
import { store } from "../state/store";
import { el } from "../util/dom";

let drawerEl: HTMLElement | null = null;

export function openSearchDrawer(query: string, nodeId?: string): void {
  closeSearchDrawer();
  drawerEl = el("aside", "search-drawer");
  const head = el("div", "search-drawer-head");
  head.appendChild(el("h3", "", "向量检索"));
  const closeBtn = el("button", "", "✕");
  closeBtn.type = "button";
  closeBtn.onclick = closeSearchDrawer;
  head.appendChild(closeBtn);
  drawerEl.appendChild(head);

  const qInput = document.createElement("input");
  qInput.value = query;
  qInput.className = "search-drawer-input";
  drawerEl.appendChild(qInput);

  const results = el("div", "search-drawer-results");
  drawerEl.appendChild(results);

  const run = async () => {
    results.textContent = "检索中…";
    try {
      const res = await corpus.searchPreview(qInput.value);
      results.innerHTML = "";
      const hits = (res as { hits?: unknown[] }).hits || (res as { chunks?: unknown[] }).chunks || [];
      if (!Array.isArray(hits) || !hits.length) {
        results.appendChild(el("p", "muted", "无结果"));
        return;
      }
      hits.slice(0, 10).forEach((hit) => {
        const h = hit as Record<string, unknown>;
        const item = el("div", "search-hit");
        const snippet = String(h.text || h.summary || h.label || JSON.stringify(h)).slice(0, 200);
        item.appendChild(el("p", "", snippet));
        const nid = String(h.node_id || h.id || nodeId || "");
        if (nid && !nid.startsWith("{")) {
          const open = el("button", "btn-sm", "打开节点");
          open.type = "button";
          open.onclick = () => {
            closeSearchDrawer();
            store.openNode(nid);
          };
          item.appendChild(open);
        }
        results.appendChild(item);
      });
    } catch (err) {
      results.textContent = String(err);
    }
  };

  qInput.onkeydown = (e) => {
    if (e.key === "Enter") run();
  };
  const searchBtn = el("button", "btn-primary", "检索");
  searchBtn.type = "button";
  searchBtn.onclick = run;
  drawerEl.appendChild(searchBtn);

  document.body.appendChild(drawerEl);
  run();
}

export function closeSearchDrawer(): void {
  drawerEl?.remove();
  drawerEl = null;
}
