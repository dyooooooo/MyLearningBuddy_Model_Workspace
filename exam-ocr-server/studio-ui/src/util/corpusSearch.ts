import { corpus } from "../api/corpus";
import { store } from "../state/store";
import { el } from "./dom";
import { nodeTypeLabel, QUEUE_LABELS, statusLabel } from "./labels";
import { comparePageOrder } from "./pageSort";
import { openSearchDrawer } from "../views/SearchDrawer";

export type SearchTab = "nodes" | "vector" | "queue";

export async function populateSearchResults(
  results: HTMLElement,
  tab: SearchTab,
  q: string,
  onItemSelect?: () => void,
): Promise<void> {
  results.innerHTML = "";

  if (tab === "queue") {
    const wq = await corpus.workqueue();
    const items = [
      ...[...wq.draft_chunks].sort(comparePageOrder).map((n) => ({ ...n, queue: QUEUE_LABELS.draft })),
      ...wq.review_nodes.map((n) => ({ ...n, queue: QUEUE_LABELS.review })),
      ...wq.unindexed_published.map((n) => ({ ...n, queue: "未索引" })),
    ].filter((n) => !q || n.label.includes(q) || n.id.includes(q));
    items.slice(0, 20).forEach((n) => {
      const li = el("li", "palette-item");
      li.textContent = `[${n.queue}] ${n.label}`;
      li.onclick = () => {
        onItemSelect?.();
        store.openNode(n.id);
      };
      results.appendChild(li);
    });
    return;
  }

  if (!q) return;

  if (tab === "nodes") {
    const nodes = await corpus.searchNodes(q);
    nodes.slice(0, 15).forEach((n) => {
      const li = el("li", "palette-item");
      li.textContent = `${n.label}（${nodeTypeLabel(n.node_type)}）· ${statusLabel(n.status)}`;
      li.onclick = () => {
        onItemSelect?.();
        store.openNode(n.id);
      };
      results.appendChild(li);
    });
  }

  if (tab === "vector") {
    const vecLi = el("li", "palette-item vec");
    vecLi.textContent = `打开试搜面板: "${q}"`;
    vecLi.onclick = () => {
      onItemSelect?.();
      openSearchDrawer(q);
    };
    results.appendChild(vecLi);
    try {
      const res = await corpus.searchPreview(q);
      const hits = (res as { hits?: { text?: string; node_id?: string }[] }).hits || [];
      hits.slice(0, 5).forEach((h) => {
        const li = el("li", "palette-item");
        li.textContent = String(h.text || h.node_id || "").slice(0, 80);
        if (h.node_id) {
          li.onclick = () => {
            onItemSelect?.();
            store.openNode(h.node_id!);
          };
        }
        results.appendChild(li);
      });
    } catch {
      /* ignore preview errors */
    }
  }
}
