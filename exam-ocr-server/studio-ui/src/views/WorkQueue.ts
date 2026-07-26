import { corpus } from "../api/corpus";
import type { WorkQueueData, WorkQueueNode } from "../api/types";
import { store } from "../state/store";
import { el, statusClass } from "../util/dom";
import { jobStatusLabel } from "../util/labels";
import { comparePageOrder } from "../util/pageSort";

export function mountWorkQueuePanel(container: HTMLElement): void {
  container.innerHTML = "";
  container.classList.add("work-queue", "work-queue-panel");
  void render(container);
  document.addEventListener("studio:workqueue-refresh", () => {
    void render(container);
  });
  document.addEventListener("studio:import-chunk-ready", () => {
    void render(container);
  });
}

async function render(target: HTMLElement): Promise<void> {
  target.appendChild(el("p", "muted", "加载…"));
  try {
    const wq: WorkQueueData = await corpus.workqueue();
    target.innerHTML = "";
    target.appendChild(
      el(
        "p",
        "muted wq-intro",
        "在此完成审核与发布，不必先打开节点。正式发布后才会进入检索库。",
      ),
    );
    const draftChunks = [...wq.draft_chunks].sort(comparePageOrder);
    appendQueue(target, "草稿 · 待提交审核", draftChunks, "draft");
    appendQueue(target, "已审核 · 待正式发布", wq.review_nodes, "review");
    appendQueue(target, "已发布但搜不到（未进检索库）", wq.unindexed_published, "unindexed");
    if (wq.structure_review_imports?.length) {
      target.appendChild(el("h5", "wq-warn", `章节待确认 (${wq.structure_review_imports.length})`));
      wq.structure_review_imports.forEach((j) => {
        const row = el("div", "wq-item wq-item-row");
        row.appendChild(el("span", "", j.label || j.id));
        const open = el("button", "btn-sm", "看导入") as HTMLButtonElement;
        open.type = "button";
        open.onclick = () => store.showImportHub();
        row.appendChild(open);
        target.appendChild(row);
      });
    }
    if (wq.failed_imports.length) {
      target.appendChild(el("h5", "", "导入失败"));
      wq.failed_imports.forEach((j) => {
        const row = el("div", "wq-item error");
        row.textContent = `${j.label}：${j.error || jobStatusLabel(j.status)}`;
        target.appendChild(row);
      });
    }
    if (wq.inbox_ready.length) {
      target.appendChild(el("h5", "", "收件箱待审"));
      wq.inbox_ready.forEach((it) => {
        const row = el("button", "wq-item link-btn", it.title);
        row.type = "button";
        row.onclick = () => {
          const nid = it.proposed_node_ids?.[0];
          if (nid) void store.openNode(nid);
        };
        target.appendChild(row);
      });
    }
    const hasRows = target.querySelector(".wq-item, .wq-item-row");
    if (!hasRows) {
      target.appendChild(el("p", "muted", "暂无待办。可先去「导入」上传 PDF。"));
    }
  } catch (err) {
    target.innerHTML = "";
    const box = el("div", "studio-inline-error");
    box.appendChild(el("p", "error", String(err)));
    const retry = el("button", "btn-secondary", "重试") as HTMLButtonElement;
    retry.type = "button";
    retry.onclick = () => void render(target);
    box.appendChild(retry);
    target.appendChild(box);
  }
}

function appendQueue(
  body: HTMLElement,
  title: string,
  items: WorkQueueNode[],
  kind: "draft" | "review" | "unindexed",
): void {
  if (!items.length) return;
  body.appendChild(el("h5", kind === "unindexed" ? "wq-warn" : "", `${title} (${items.length})`));
  items.slice(0, 20).forEach((n) => {
    const row = el("div", `wq-item wq-item-row ${statusClass(n.status)}`);
    const open = el("button", "link-btn wq-label", n.label || n.id) as HTMLButtonElement;
    open.type = "button";
    open.title = "打开节点";
    open.onclick = () => void store.openNode(n.id);
    const actions = el("div", "wq-actions");
    if (kind === "draft") {
      actions.appendChild(
        actionBtn("提交审核", async () => {
          await corpus.submitReview(n.id);
          document.dispatchEvent(new CustomEvent("studio:workqueue-refresh"));
        }),
      );
    } else if (kind === "review") {
      actions.appendChild(
        actionBtn("正式发布", async () => {
          if (!confirm(`正式发布「${n.label}」？发布后可进入检索库。`)) return;
          await corpus.publish(n.id);
          document.dispatchEvent(new CustomEvent("studio:workqueue-refresh"));
        }),
      );
    } else if (kind === "unindexed") {
      actions.appendChild(
        actionBtn("补进检索库", async () => {
          await corpus.publish(n.id);
          document.dispatchEvent(new CustomEvent("studio:workqueue-refresh"));
        }),
      );
    }
    row.append(open, actions);
    body.appendChild(row);
  });
}

function actionBtn(label: string, fn: () => Promise<void>): HTMLButtonElement {
  const btn = el("button", "btn-sm", label) as HTMLButtonElement;
  btn.type = "button";
  btn.onclick = async (ev) => {
    ev.stopPropagation();
    const prev = btn.textContent || label;
    btn.disabled = true;
    btn.textContent = "…";
    try {
      await fn();
    } catch (e) {
      alert(String(e));
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  };
  return btn;
}
