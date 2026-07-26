import { corpus } from "../api/corpus";
import type { CorpusNode } from "../api/types";
import { mountTreeView } from "./TreeView";
import { el } from "../util/dom";

export function openKpPicker(node: CorpusNode, onBound: () => void): void {
  const overlay = el("div", "modal-overlay kp-picker-overlay");
  const box = el("div", "modal-box kp-picker");
  box.appendChild(el("h3", "", "选择考纲"));

  const treeBox = el("div", "kp-tree");
  box.appendChild(treeBox);

  const close = el("button", "", "取消");
  close.type = "button";
  close.onclick = () => overlay.remove();
  box.appendChild(close);
  overlay.appendChild(box);
  document.body.appendChild(overlay);

  mountTreeView(treeBox, {
    root: "kp",
    mode: "select",
    onSelect: async (n) => {
      const rel = node.node_type === "chunk" ? "covers_kp" : "defines_kp";
      await corpus.patchNode(node.id, {
        links: { ...node.links, primary_kp: n.id },
      } as Partial<CorpusNode>);
      await corpus.addEdge(node.id, n.id, rel);
      overlay.remove();
      onBound();
    },
  });
}
