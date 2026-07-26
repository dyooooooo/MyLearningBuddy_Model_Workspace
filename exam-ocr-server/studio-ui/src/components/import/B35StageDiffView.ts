import type { ChangeEntry, PageDebugChangelog } from "../../api/import35b";
import { el } from "../../util/dom";

export function mountB35StageDiffView(
  container: HTMLElement,
  changelog: PageDebugChangelog | null,
): void {
  container.innerHTML = "";
  if (!changelog) {
    container.appendChild(
      el("p", "b35-debug-muted", "无 changelog（旧任务或未开启 debug 落盘）"),
    );
    return;
  }

  const transitions = Object.entries(changelog.by_transition || {});
  if (!changelog.total_changes) {
    container.appendChild(el("p", "b35-debug-muted", changelog.summary_zh));
    return;
  }

  for (const [key, group] of transitions) {
    if (!group.count) continue;
    const section = document.createElement("details");
    section.className = "b35-change-transition";
    section.open = key === "raw_model→repaired_model";
    const summary = document.createElement("summary");
    summary.textContent = `${group.stage_label}（${group.count} 处）`;
    section.appendChild(summary);
    const list = el("div", "b35-change-list");
    for (const item of group.changes) {
      list.appendChild(renderChangeRow(item));
    }
    section.appendChild(list);
    container.appendChild(section);
  }
}

function renderChangeRow(item: ChangeEntry): HTMLElement {
  const row = el("div", "b35-change-row");
  const head = el("button", "b35-change-head");
  head.type = "button";
  head.textContent = item.summary_zh || item.path;
  const body = el("div", "b35-change-body hidden");
  body.appendChild(el("div", "b35-change-meta muted", `${item.path} · ${item.reason}`));
  if (item.before) {
    const before = el("pre", "b35-change-before", item.before);
    body.appendChild(before);
  }
  if (item.after) {
    const after = el("pre", "b35-change-after", item.after);
    body.appendChild(after);
  }
  head.onclick = () => body.classList.toggle("hidden");
  row.append(head, body);
  return row;
}
