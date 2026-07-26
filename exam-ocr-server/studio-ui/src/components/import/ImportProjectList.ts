// 导入首页：中间区展示全部 MinerU + 35B 任务，点击进入预览

import { corpus } from "../../api/corpus";
import { import35b, type Import35bJob } from "../../api/import35b";
import type { ImportJob } from "../../api/types";
import { refreshVault } from "../SidePanel";
import { store } from "../../state/store";
import { el } from "../../util/dom";
import { jobStatusLabel } from "../../util/labels";
import {
  B35_IMPORT_LINE,
  B35_JOB_DELETED,
  B35_NAVIGATE,
  B35_PREVIEW_STATE,
  deleteB35ImportJob,
} from "./B35ImportTree";

type Line = "mineru" | "b35";

const RUNNING = new Set([
  "uploaded",
  "queued",
  "pending",
  "converting",
  "parsing",
  "running",
  "building",
  "chunking",
  "embedding",
]);

interface ProjectRow {
  line: Line;
  id: string;
  label: string;
  status: string;
  hint: string;
}

export function mountImportProjectList(container: HTMLElement): { refresh: () => void } {
  const wrap = el("div", "import-hub-list");
  wrap.appendChild(el("h2", "import-hub-title", "导入任务"));
  const list = el("div", "import-jobs-list");
  const empty = el("p", "import-hub-empty muted hidden", "");
  wrap.append(list, empty);
  container.appendChild(wrap);

  let activeKey = "";
  let refreshGen = 0;

  function rowKey(line: Line, id: string): string {
    return `${line}:${id}`;
  }

  function openProject(row: ProjectRow): void {
    store.showImportPreview();
    activeKey = rowKey(row.line, row.id);
    if (row.line === "b35") {
      document.dispatchEvent(new CustomEvent(B35_IMPORT_LINE));
      document.dispatchEvent(
        new CustomEvent(B35_NAVIGATE, { detail: { jobId: row.id, label: row.label } }),
      );
    } else {
      void import("./activeMinerUJob").then(({ focusMinerUJobTriPane }) => {
        focusMinerUJobTriPane(row.id);
      });
      void corpus.importJob(row.id).then((job) => store.setImportJob(job)).catch(() => undefined);
    }
    paintActive();
  }

  async function cancelProject(row: ProjectRow): Promise<void> {
    if (row.line !== "mineru") return;
    if (!confirm(`取消 MinerU 导入任务「${row.label}」？`)) return;
    try {
      await corpus.cancelImport(row.id);
      void refresh();
      void refreshVault();
    } catch (e) {
      alert(`取消失败：${(e as Error).message}`);
    }
  }

  async function deleteProject(row: ProjectRow): Promise<void> {
    if (row.line === "b35") {
      const ok = await deleteB35ImportJob(row.id, row.label);
      if (ok) void refresh();
      return;
    }
    if (
      !confirm(
        `删除 MinerU 导入任务「${row.label}」？\n（将同时删除与之绑定的 AI 对话）`,
      )
    ) {
      return;
    }
    try {
      await corpus.deleteImport(row.id);
      if (activeKey === rowKey(row.line, row.id)) activeKey = "";
      void import("../CopilotPanel").then(({ onImportJobDeleted }) => {
        onImportJobDeleted(row.id);
      });
      document.dispatchEvent(
        new CustomEvent("studio:mineru-import-deleted", { detail: { jobId: row.id } }),
      );
      void refresh();
      void refreshVault();
    } catch (e) {
      alert(`删除失败：${(e as Error).message}`);
    }
  }

  function renderRow(row: ProjectRow): HTMLElement {
    const rowWrap = el("div", "import-project-row");
    rowWrap.dataset.key = rowKey(row.line, row.id);

    const item = el("button", "import-project-item");
    item.type = "button";
    const head = el("div", "import-project-head");
    head.appendChild(
      el(
        "span",
        `import-project-line import-project-line--${row.line}`,
        row.line === "b35" ? "35B" : "MinerU",
      ),
    );
    head.appendChild(el("span", "import-job-title", row.label || row.id));
    item.appendChild(head);
    const status = jobStatusLabel(row.status) + (row.hint ? ` · ${row.hint}` : "");
    item.appendChild(el("div", "import-job-status muted", status));
    item.onclick = () => openProject(row);
    rowWrap.appendChild(item);

    const actions = el("div", "import-project-actions");
    if (row.line === "mineru" && RUNNING.has(row.status)) {
      const cancelBtn = el("button", "import-project-action", "取消") as HTMLButtonElement;
      cancelBtn.type = "button";
      cancelBtn.title = "取消进行中的导入";
      cancelBtn.onclick = (e) => {
        e.stopPropagation();
        void cancelProject(row);
      };
      actions.appendChild(cancelBtn);
    }
    const delBtn = el("button", "import-project-action danger", "删除") as HTMLButtonElement;
    delBtn.type = "button";
    delBtn.title = "删除此导入任务";
    delBtn.onclick = (e) => {
      e.stopPropagation();
      void deleteProject(row);
    };
    actions.appendChild(delBtn);
    rowWrap.appendChild(actions);

    return rowWrap;
  }

  function paintActive(): void {
    list.querySelectorAll<HTMLElement>(".import-project-row").forEach((n) => {
      n.classList.toggle("active", n.dataset.key === activeKey);
    });
  }

  function toRows(b35: Import35bJob[], mineru: ImportJob[]): ProjectRow[] {
    const b35Rows: ProjectRow[] = b35.map((j) => ({
      line: "b35",
      id: j.id,
      label: j.label || j.id,
      status: j.status,
      hint: String(j.meta?.progress || j.error || ""),
    }));
    const mineruRows: ProjectRow[] = mineru.map((j) => ({
      line: "mineru",
      id: j.id,
      label: j.label || j.id,
      status: j.status || "",
      hint: "",
    }));
    return [...b35Rows, ...mineruRows]
      .filter((row, i, arr) => arr.findIndex((r) => r.line === row.line && r.id === row.id) === i)
      .sort((a, b) => a.label.localeCompare(b.label, "zh"));
  }

  async function refresh(): Promise<void> {
    const gen = ++refreshGen;
    list.innerHTML = "";
    empty.classList.add("hidden");
    let rows: ProjectRow[] = [];
    try {
      const [b35, mineru] = await Promise.all([
        import35b.jobs().catch(() => [] as Import35bJob[]),
        corpus.importJobs().catch(() => [] as ImportJob[]),
      ]);
      if (gen !== refreshGen) return;
      rows = toRows(b35, mineru);
    } catch (e) {
      if (gen !== refreshGen) return;
      empty.textContent = `加载失败：${(e as Error).message}`;
      empty.classList.remove("hidden");
      return;
    }
    if (gen !== refreshGen) return;
    if (!rows.length) {
      empty.textContent = "暂无导入任务。请使用左侧「+ 新建 PDF 导入」创建。";
      empty.classList.remove("hidden");
      return;
    }
    for (const row of rows) list.appendChild(renderRow(row));
    paintActive();
  }

  document.addEventListener(B35_PREVIEW_STATE, ((e: CustomEvent<{ active?: boolean; jobId?: string }>) => {
    const d = e.detail;
    if (d?.active && d.jobId) activeKey = rowKey("b35", d.jobId);
    else if (!d?.active && store.importCenterView === "hub") activeKey = "";
    paintActive();
  }) as EventListener);

  document.addEventListener("studio:mineru-preview", ((e: CustomEvent<{ jobId: string }>) => {
    if (e.detail?.jobId) activeKey = rowKey("mineru", e.detail.jobId);
    paintActive();
  }) as EventListener);

  document.addEventListener("studio:import-hub", () => {
    activeKey = "";
    void refresh();
  });

  document.addEventListener(B35_JOB_DELETED, () => {
    if (store.importCenterView === "hub") void refresh();
  });

  document.addEventListener("studio:import-workspace-focus", () => {
    if (store.importCenterView === "hub") void refresh();
  });

  void refresh();

  return { refresh: () => void refresh() };
}
