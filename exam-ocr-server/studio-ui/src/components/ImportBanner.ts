import { store } from "../state/store";
import { el } from "../util/dom";
import {
  describeImportJob,
  getActiveImportJobId,
  IMPORT_JOB_TERMINAL,
} from "../services/importProgress";

let bannerEl: HTMLElement | null = null;

export function mountImportBanner(container: HTMLElement): void {
  store.subscribeSelector(
    (s) => `${s.activeImportJob?.id || ""}|${s.activeImportJob?.status || ""}|${s.activeRibbon}`,
    () => render(container),
  );
  document.addEventListener("studio:import-progress", () => render(container));
}

/** 横幅只表示「还在跑」：终态（chunked/done/…）或离开导入区时不显示。 */
function shouldShowBanner(): boolean {
  const job = store.activeImportJob;
  // 终态优先：打开已完成任务时 observer 可能尚未把 activeJobId 清掉，
  // 不能因为「还在轮询」就把横幅钉死。
  if (job && IMPORT_JOB_TERMINAL.has(String(job.status || ""))) return false;
  // 非导入 Ribbon 时不打扰（改进点 / 方案 / 教材等）
  if (store.activeRibbon !== "import") return false;
  if (getActiveImportJobId()) return true;
  return !!job;
}

function render(container: HTMLElement): void {
  if (!shouldShowBanner()) {
    bannerEl?.remove();
    bannerEl = null;
    return;
  }

  const job = store.activeImportJob;
  if (!bannerEl) {
    bannerEl = el("div", "import-banner");
    container.prepend(bannerEl);
  }

  const { statusText, pct } = job
    ? describeImportJob(job)
    : { statusText: "导入任务进行中…", pct: 15 };

  bannerEl.innerHTML = "";
  bannerEl.appendChild(el("span", "import-banner-text", statusText));

  const missing = Array.isArray(job?.meta?.missing_pages) ? job!.meta!.missing_pages! : [];
  if (missing.length > 0) {
    const warn = el(
      "div",
      "import-banner-missing",
      `已确认缺页（不是还在跑）：${missing.join(", ")}`,
    );
    warn.style.color = "var(--yellow)";
    warn.style.fontSize = "0.8rem";
    warn.style.marginTop = "0.25rem";
    bannerEl.appendChild(warn);
  }

  const barWrap = el("div", "import-banner-progress");
  const bar = el("div", "import-banner-bar");
  bar.style.width = `${pct}%`;
  barWrap.appendChild(bar);
  bannerEl.appendChild(barWrap);

  const actions = el("div", "import-banner-actions");
  const openBtn = el("button", "btn-link", "导入详情");
  openBtn.type = "button";
  openBtn.onclick = () => document.dispatchEvent(new CustomEvent("studio:import-wizard"));
  actions.appendChild(openBtn);
  bannerEl.appendChild(actions);
}
