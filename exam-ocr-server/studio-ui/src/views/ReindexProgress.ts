import { corpus } from "../api/corpus";
import type { ReindexJobStatus } from "../api/types";
import { el } from "../util/dom";
import { reindexStatusLabel } from "../util/labels";

export async function runReindexWithProgress(onDone?: () => void): Promise<void> {
  const wq = await corpus.workqueue();
  const count = wq.review_nodes.length;
  if (
    count > 0 &&
    !confirm(`将正式发布并排入向量库 ${count} 个待发布节点，继续？`)
  ) {
    return;
  }
  if (count === 0) {
    alert("当前没有待发布节点。请先将草稿提交审核并通过语言校验。");
    return;
  }

  const overlay = el("div", "modal-overlay");
  const box = el("div", "modal-box reindex-progress");
  box.appendChild(el("h2", "", "批量索引进度"));
  const barWrap = el("div", "progress-bar-wrap");
  const bar = el("div", "progress-bar");
  barWrap.appendChild(bar);
  box.appendChild(barWrap);
  const statusText = el("p", "", "排队中…");
  box.appendChild(statusText);
  const errBox = el("pre", "reindex-errors hidden");
  box.appendChild(errBox);
  overlay.appendChild(box);
  document.body.appendChild(overlay);

  const job = await corpus.reindexAsync();
  let done = false;

  const poll = async (): Promise<void> => {
    const st: ReindexJobStatus = await corpus.reindexJob(job.job_id);
    const pct = st.total ? Math.round((st.done / st.total) * 100) : 0;
    bar.style.width = `${pct}%`;
    statusText.textContent = `${reindexStatusLabel(st.status)} · ${st.done}/${st.total}`;
    if (st.errors?.length) {
      errBox.classList.remove("hidden");
      errBox.textContent = st.errors.join("\n");
    }
    if (st.status === "completed" || st.status === "completed_with_errors") {
      done = true;
      setTimeout(() => {
        overlay.remove();
        onDone?.();
      }, 800);
      return;
    }
    setTimeout(poll, 1500);
  };

  poll().catch((err) => {
    statusText.textContent = String(err);
  });
}
