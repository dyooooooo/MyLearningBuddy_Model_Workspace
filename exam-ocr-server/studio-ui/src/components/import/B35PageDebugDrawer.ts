import {
  import35b,
  type PageDebugArtifactId,
  type PageDebugChangelog,
  type PageDebugIndex,
} from "../../api/import35b";
import { el } from "../../util/dom";
import { mountB35StageDiffView } from "./B35StageDiffView";

const STAGE_PANELS: { id: PageDebugArtifactId | "page_image"; label: string }[] = [
  { id: "page_image", label: "输入页图" },
  { id: "raw_model", label: "① 模型原始 JSON（未修复）" },
  { id: "post_repair", label: "② 防腐层后 Blueprint" },
  { id: "post_mfr", label: "③ MFR 后 Blueprint" },
  { id: "post_crop", label: "④ 裁剪后 Blueprint" },
  { id: "blueprint", label: "⑤ 最终 Blueprint（Normalize 后）" },
  { id: "html", label: "⑥ 编译 HTML 源码" },
  { id: "ssr_report", label: "⑦ SSR 公式渲染报告" },
  { id: "meta", label: "⑧ 校验疑点与质量指标" },
];

export function mountB35PageDebugDrawer(
  container: HTMLElement,
  jobId: string,
  pageNo: number,
): void {
  const root = document.createElement("details");
  root.className = "b35-page-debug-drawer";
  const summary = document.createElement("summary");
  summary.textContent = "Pipeline 调试输出";
  root.appendChild(summary);

  const inner = el("div", "b35-page-debug-inner");
  const summaryBar = el("div", "b35-debug-summary-bar muted", "展开后加载…");
  const changesTitle = el("h4", "b35-debug-section-title", "相对模型原始的修改");
  const changesHost = el("div", "b35-debug-changes");
  const stagesHost = el("div", "b35-debug-stages");
  inner.append(summaryBar, changesTitle, changesHost, stagesHost);
  root.appendChild(inner);
  container.appendChild(root);

  let loaded = false;
  let index: PageDebugIndex | null = null;
  let changelog: PageDebugChangelog | null = null;

  root.addEventListener("toggle", () => {
    if (!root.open || loaded) return;
    loaded = true;
    void loadIndex();
  });

  async function loadIndex(): Promise<void> {
    summaryBar.textContent = "加载中…";
    try {
      index = await import35b.pageDebugIndex(jobId, pageNo);
      summaryBar.textContent = index.changelog_summary.summary_zh || "—";
      summaryBar.classList.toggle(
        "b35-debug-summary-bar--warn",
        (index.changelog_summary.total_changes || 0) > 0,
      );
      try {
        changelog = await import35b.pageDebugChangelog(jobId, pageNo);
      } catch {
        changelog = null;
      }
      mountB35StageDiffView(changesHost, changelog);
      buildStagePanels();
    } catch (e) {
      summaryBar.textContent = `加载失败：${(e as Error).message}`;
    }
  }

  function buildStagePanels(): void {
    stagesHost.innerHTML = "";
    if (!index) return;
    const byTransition = changelog?.by_transition || {};
    for (const panel of STAGE_PANELS) {
      const details = document.createElement("details");
      details.className = "b35-debug-stage-panel";
      const sum = document.createElement("summary");
      let badge = "";
      if (panel.id !== "page_image" && panel.id !== "meta" && panel.id !== "raw_model") {
        const tKey = Object.keys(byTransition).find((k) => k.endsWith(`→${panel.id}`));
        if (tKey && byTransition[tKey]?.count) {
          badge = ` · 本阶段 ${byTransition[tKey].count} 处变更`;
        }
      }
      sum.textContent = panel.label + badge;
      details.appendChild(sum);
      const body = el("div", "b35-debug-stage-body", "点击展开加载…");
      details.appendChild(body);
      details.addEventListener("toggle", () => {
        if (!details.open || body.dataset.loaded) return;
        void loadPanel(panel, body);
      });
      const avail =
        panel.id === "page_image"
          ? index!.artifacts.page_image?.available
          : index!.artifacts[panel.id]?.available;
      if (avail === false) {
        body.textContent = "该阶段快照不可用（旧任务或未开启 debug）";
        body.dataset.loaded = "1";
      }
      stagesHost.appendChild(details);
    }
  }

  async function loadPanel(
    panel: { id: PageDebugArtifactId | "page_image"; label: string },
    body: HTMLElement,
  ): Promise<void> {
    body.innerHTML = "";
    body.textContent = "加载中…";
    try {
      if (panel.id === "page_image") {
        const img = document.createElement("img");
        img.className = "b35-debug-page-image";
        img.alt = `第 ${pageNo} 页输入图`;
        img.src = import35b.pageDebugImageUrl(jobId, pageNo);
        body.innerHTML = "";
        body.appendChild(img);
      } else if (panel.id === "html") {
        const text = (await import35b.pageDebugArtifact(jobId, pageNo, "html")) as string;
        body.appendChild(el("pre", "b35-debug-pre", text));
      } else {
        const data = await import35b.pageDebugArtifact(jobId, pageNo, panel.id);
        body.appendChild(
          el("pre", "b35-debug-pre", JSON.stringify(data, null, 2)),
        );
      }
      body.dataset.loaded = "1";
    } catch (e) {
      body.textContent = `加载失败：${(e as Error).message}`;
    }
  }
}
