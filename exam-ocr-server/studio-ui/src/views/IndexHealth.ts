import { corpus } from "../api/corpus";
import { store } from "../state/store";
import { el } from "../util/dom";
import { statusLabel } from "../util/labels";
import { runReindexWithProgress } from "./ReindexProgress";

export function mountIndexHealth(slot?: HTMLElement): void {
  const render = async (target: HTMLElement) => {
    const s = await corpus.stats();
    store.stats = s;
    target.innerHTML = "";
    target.appendChild(el("h4", "", "检索库状态"));
    const unindexed = s.published_unindexed ?? 0;
    const warn = unindexed > 0 || !s.milvus_schema_ok ? " health-warn" : "";
    target.className = "index-health" + warn;
    target.appendChild(el("div", "", `检索库条目: ${s.milvus_production_count ?? 0}`));
    if (unindexed > 0) {
      target.appendChild(
        el(
          "div",
          "health-warn",
          `已发布但搜不到: ${unindexed}（需补进检索库）`,
        ),
      );
    } else {
      target.appendChild(el("div", "", "已发布内容均已进检索库"));
    }
    if (!s.milvus_schema_ok) {
      target.appendChild(el("div", "error", "检索库结构异常，请联系维护"));
    }
    const btn = el("button", "btn-sm", "把已审核内容写入检索库");
    btn.type = "button";
    btn.onclick = () => runReindexWithProgress(() => render(target));
    target.appendChild(btn);
  };

  document.addEventListener("studio:mount-index-health", ((e: CustomEvent) => {
    render(e.detail as HTMLElement);
  }) as EventListener);

  if (slot) render(slot);
}

export function mountInboxPanel(rootEl: HTMLElement): void {
  corpus.inbox().then((items) => {
    rootEl.innerHTML = "";
    rootEl.appendChild(el("h3", "", "收件箱"));
    const ul = el("ul", "inbox-list");
    items.forEach((it) => {
      const li = el("li", "inbox-item");
      li.appendChild(el("strong", "", it.title));
      li.appendChild(el("span", "badge", statusLabel(it.status)));
      const actions = el("div", "inbox-actions");
      const approve = el("button", "", "批准");
      approve.type = "button";
      approve.onclick = async () => {
        await corpus.approveInbox(it.id);
        mountInboxPanel(rootEl);
      };
      const reject = el("button", "", "拒绝");
      reject.type = "button";
      reject.onclick = async () => {
        await corpus.rejectInbox(it.id);
        mountInboxPanel(rootEl);
      };
      actions.append(approve, reject);
      li.appendChild(actions);
      ul.appendChild(li);
    });
    rootEl.appendChild(ul);
  });
}
