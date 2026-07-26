import { corpus } from "../api/corpus";
import { store } from "../state/store";
import { el } from "../util/dom";
import { nodeTypeLabel, relTypeLabel, statusLabel } from "../util/labels";

type Network = import("vis-network/standalone").Network;

const TYPE_COLORS: Record<string, string> = {
  textbook: "#6366f1",
  section: "#8b5cf6",
  chunk: "#22c55e",
  kp: "#f59e0b",
  method: "#06b6d4",
  question: "#ef4444",
  shortcut: "#ec4899",
};

const STRUCTURAL_RELS = "contains_section,contains_chunk,in_textbook,in_section,defines_kp,covers_kp";
const KP_RELS = "defines_kp,covers_kp,applies_to_kp,derived_from_kp";
const TAG_RELS = "tagged_with,has_tag,tag";

let network: Network | null = null;
let filterStructural = true;
let filterKp = true;
let filterTags = false;
let useStructuralGraph = false;

export function mountGraphPanel(rootEl: HTMLElement): void {
  document.addEventListener("studio:structural-graph", () => {
    useStructuralGraph = true;
    filterStructural = true;
    filterKp = true;
    filterTags = false;
  });

  store.subscribeSelector(
    (s) => `${s.showGraph}|${s.currentNode?.id || ""}`,
    () => render(),
  );
  render();

  function currentRelTypes(): string | undefined {
    const parts: string[] = [];
    if (filterStructural) parts.push(...STRUCTURAL_RELS.split(","));
    if (filterKp) parts.push(...KP_RELS.split(","));
    if (filterTags) parts.push(...TAG_RELS.split(","));
    return parts.length ? [...new Set(parts)].join(",") : undefined;
  }

  function textbookRoot(): string | undefined {
    const node = store.currentNode;
    if (!node) return undefined;
    const tb = node.links?.textbook_id || (node.node_type === "textbook" ? node.id : null);
    if (tb) return `textbook:${tb}`;
    return undefined;
  }

  function destroyNetwork(): void {
    if (network) {
      try {
        network.destroy();
      } catch {
        /* ignore */
      }
      network = null;
    }
  }

  function render(): void {
    if (!store.showGraph) {
      destroyNetwork();
      rootEl.classList.add("hidden");
      rootEl.innerHTML = "";
      return;
    }
    destroyNetwork();
    rootEl.classList.remove("hidden");
    rootEl.innerHTML = "";

    const toolbar = el("div", "graph-toolbar");
    const close = el("button", "", "✕ 关闭");
    close.type = "button";
    close.onclick = () => {
      useStructuralGraph = false;
      store.toggleGraph(false);
    };
    toolbar.appendChild(el("span", "", "知识图谱"));

    const cbStruct = document.createElement("input");
    cbStruct.type = "checkbox";
    cbStruct.checked = filterStructural;
    cbStruct.onchange = () => {
      filterStructural = cbStruct.checked;
      render();
    };
    const cbKp = document.createElement("input");
    cbKp.type = "checkbox";
    cbKp.checked = filterKp;
    cbKp.onchange = () => {
      filterKp = cbKp.checked;
      render();
    };
    const cbTags = document.createElement("input");
    cbTags.type = "checkbox";
    cbTags.checked = filterTags;
    cbTags.onchange = () => {
      filterTags = cbTags.checked;
      render();
    };

    toolbar.append(
      el("label", "graph-filter", ""),
      cbStruct,
      el("span", "", "结构边"),
      cbKp,
      el("span", "", "考纲边"),
      cbTags,
      el("span", "", "标签"),
      close,
    );
    rootEl.appendChild(toolbar);

    const container = el("div", "graph-canvas");
    rootEl.appendChild(container);

    const center = store.currentNode?.id || "";
    const root = useStructuralGraph || textbookRoot() ? textbookRoot() : undefined;
    const relTypes = useStructuralGraph ? STRUCTURAL_RELS : currentRelTypes();

    void Promise.all([
      corpus.graph(center, true, { root, relTypes }),
      import("vis-network/standalone"),
    ]).then(([data, vis]) => {
      if (!store.showGraph) return;
      const nodes = data.nodes.map((n) => ({
        id: n.id,
        label: n.label?.slice(0, 24) || n.id.slice(0, 16),
        color: TYPE_COLORS[n.node_type] || "#64748b",
        title: `${nodeTypeLabel(n.node_type)} · ${statusLabel(n.status)}`,
      }));
      const edges = data.edges.map((e) => ({
        id: e.id,
        from: e.from_id,
        to: e.to_id,
        label: relTypeLabel(e.rel_type),
        arrows: "to",
        font: { size: 10, align: "middle" },
      }));
      network = new vis.Network(
        container,
        { nodes, edges },
        {
          physics: { stabilization: true, barnesHut: { gravitationalConstant: -8000 } },
          interaction: { hover: true },
        },
      );
      network.on("click", (params) => {
        if (params.nodes.length) void store.openNode(String(params.nodes[0]));
      });
    });
  }
}
