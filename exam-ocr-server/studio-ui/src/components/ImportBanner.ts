import { store } from "../state/store";
import { el } from "../util/dom";
import { describeImportJob, getActiveImportJobId } from "../services/importProgress";

let bannerEl: HTMLElement | null = null;

export function mountImportBanner(container: HTMLElement): void {
  store.subscribeSelector(
    (s) => s.activeImportJob,
    () => render(container),
  );
  document.addEventListener("studio:import-progress", () => render(container));
}

function render(container: HTMLElement): void {
  const job = store.activeImportJob;
  const polling = getActiveImportJobId();
  if (!job && !polling) {
    bannerEl?.remove();
    bannerEl = null;
    return;
  }

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
