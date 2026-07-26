// Right-rail workflow + PipeDebug for import preview (MinerU).

import { el } from "../../util/dom";
import { store } from "../../state/store";
import {
  getWorkflowSnapshot,
  onWorkflowDebug,
  type WorkflowSnapshot,
  type WorkflowStep,
} from "../../state/workflowDebug";
import { mountCorpusPageDebugDrawer } from "./CorpusPageDebugDrawer";

function fmtTime(ts: number): string {
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

function statusIcon(s: WorkflowStep["status"]): string {
  if (s === "running") return "…";
  if (s === "ok") return "✓";
  if (s === "error") return "✗";
  return "·";
}

export function mountImportWorkflowInspector(host: HTMLElement): void {
  const root = el("div", "import-workflow-inspector hidden");
  const head = el("div", "import-wf-head");
  const stepsHost = el("div", "import-wf-steps");
  const resultHost = el("div", "import-wf-result-host");
  const debugHost = el("div", "import-wf-pipedebug");
  root.append(head, stepsHost, resultHost, debugHost);
  host.appendChild(root);

  let debugMountKey = "";

  function paint(snap: WorkflowSnapshot): void {
    const inImport = store.importMode || store.activeRibbon === "import";
    root.classList.toggle("hidden", !inImport);
    if (!inImport) return;

    head.innerHTML = "";
    head.appendChild(el("h3", "inspector-title", "导入工作流"));
    const focus = snap.focus;
    const focusBox = el("div", "inspector-row import-wf-focus");
    if (focus?.jobId) {
      const page = focus.pageNo ? ` · 第 ${focus.pageNo} 页` : "";
      const blk = focus.blockId ? ` · ${focus.blockId}` : "";
      focusBox.appendChild(
        el("span", "", `${focus.line === "mineru" ? "MinerU" : "35B"}${page}`),
      );
      if (blk) focusBox.appendChild(el("div", "muted", blk));
      if (focus.label) focusBox.appendChild(el("div", "muted", focus.label));
    } else {
      focusBox.appendChild(el("span", "muted", "滚动预览或点「重新识别」后显示步骤"));
    }
    head.appendChild(focusBox);

    stepsHost.innerHTML = "";
    stepsHost.appendChild(el("div", "corpus-rerecog-label", "步骤"));
    if (!snap.steps.length) {
      stepsHost.appendChild(el("p", "muted", "尚无操作记录"));
    } else {
      for (const step of [...snap.steps].reverse()) {
        const row = el("div", `import-wf-step import-wf-step--${step.status}`);
        row.appendChild(el("span", "import-wf-step-icon", statusIcon(step.status)));
        const body = el("div", "import-wf-step-body");
        body.appendChild(
          el("div", "import-wf-step-title", `${fmtTime(step.at)} ${step.title}`),
        );
        if (step.detail) {
          body.appendChild(el("div", "muted import-wf-step-detail", step.detail));
        }
        row.appendChild(body);
        stepsHost.appendChild(row);
      }
    }

    resultHost.innerHTML = "";
    if (snap.lastResult) {
      resultHost.appendChild(el("div", "corpus-rerecog-label", "最近结果"));
      const pre = document.createElement("pre");
      pre.className = "import-wf-result corpus-debug-pre";
      try {
        const slim = { ...snap.lastResult };
        if (typeof slim.html === "string") {
          slim.html = `[html ${slim.html.length} chars]`;
        }
        pre.textContent = JSON.stringify(slim, null, 2).slice(0, 6000);
      } catch {
        pre.textContent = String(snap.lastResult);
      }
      resultHost.appendChild(pre);
    }

    if (focus?.jobId && focus.pageNo && focus.pageNo > 0) {
      const key = `${focus.jobId}:${focus.pageNo}:${focus.blockId || ""}`;
      if (key !== debugMountKey) {
        debugMountKey = key;
        debugHost.innerHTML = "";
        debugHost.appendChild(el("div", "corpus-rerecog-label", "本页 PipeDebug"));
        const slot = el("div", "import-wf-pipedebug-slot");
        debugHost.appendChild(slot);
        mountCorpusPageDebugDrawer(slot, focus.jobId, focus.pageNo, {
          autoOpen: true,
          embedded: true,
          focusBlockId: focus.blockId || "",
          summaryLabel: `MinerU PipeDebug · 第 ${focus.pageNo} 页`,
        });
      }
    } else {
      debugMountKey = "";
      debugHost.innerHTML = "";
    }
  }

  paint(getWorkflowSnapshot());
  onWorkflowDebug(paint);
  store.subscribe(() => paint(getWorkflowSnapshot()));
}
