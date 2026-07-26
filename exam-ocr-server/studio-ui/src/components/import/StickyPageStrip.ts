/**
 * 窄屏单栏时：把左栏页码导航挪到中栏顶端，横向条、不随正文滚动。
 */

import { el } from "../../util/dom";

export type StickyPageStripHandle = {
  el: HTMLElement;
  /** 刷新页码列表（与左栏页节点一致：已出稿的页） */
  setPages: (pages: number[]) => void;
  /** 高亮当前浏览页，并滚入可视区 */
  setActive: (pageNo: number) => void;
  destroy: () => void;
};

export type StickyPageStripOptions = {
  onGoto: (pageNo: number) => void;
};

export function mountStickyPageStrip(opts: StickyPageStripOptions): StickyPageStripHandle {
  const root = el("div", "studio-sticky-page-strip");
  root.setAttribute("role", "navigation");
  root.setAttribute("aria-label", "页码导航");

  const brand = el("div", "studio-sticky-page-strip__brand");
  brand.append(
    el("span", "studio-sticky-page-strip__brand-icon", "#"),
    el("span", "studio-sticky-page-strip__brand-text", "页码"),
  );
  const meta = el("span", "studio-sticky-page-strip__meta", "");

  const track = el("div", "studio-sticky-page-strip__track");
  track.setAttribute("role", "list");

  const jump = el("form", "studio-sticky-page-strip__jump") as HTMLFormElement;
  const jumpInput = document.createElement("input");
  jumpInput.type = "number";
  jumpInput.min = "1";
  jumpInput.placeholder = "页号";
  jumpInput.className = "studio-sticky-page-strip__jump-input";
  jumpInput.setAttribute("aria-label", "跳到页码");
  const jumpBtn = el("button", "studio-sticky-page-strip__jump-btn", "跳转") as HTMLButtonElement;
  jumpBtn.type = "submit";
  jump.append(jumpInput, jumpBtn);

  const left = el("div", "studio-sticky-page-strip__left");
  left.append(brand, meta);
  root.append(left, track, jump);

  let pages: number[] = [];
  let activePage = 0;
  const buttons = new Map<number, HTMLButtonElement>();

  function updateMeta(): void {
    if (!pages.length) {
      meta.textContent = "";
      return;
    }
    const cur = activePage > 0 ? activePage : "—";
    meta.textContent = `${cur} / ${pages.length}`;
  }

  function render(): void {
    track.innerHTML = "";
    buttons.clear();
    for (const pageNo of pages) {
      const btn = el("button", "studio-sticky-page-strip__btn", String(pageNo)) as HTMLButtonElement;
      btn.type = "button";
      btn.setAttribute("role", "listitem");
      btn.dataset.pageNo = String(pageNo);
      btn.title = `第 ${pageNo} 页`;
      if (pageNo === activePage) btn.classList.add("is-active");
      btn.onclick = () => opts.onGoto(pageNo);
      buttons.set(pageNo, btn);
      track.appendChild(btn);
    }
    root.classList.toggle("is-empty", pages.length === 0);
    updateMeta();
    scrollActiveIntoView();
  }

  function scrollActiveIntoView(): void {
    const btn = buttons.get(activePage);
    if (!btn) return;
    const leftPos = btn.offsetLeft - track.clientWidth / 2 + btn.offsetWidth / 2;
    track.scrollTo({ left: Math.max(0, leftPos), behavior: "smooth" });
  }

  jump.onsubmit = (ev) => {
    ev.preventDefault();
    const n = Number(jumpInput.value);
    if (n > 0) opts.onGoto(n);
  };

  return {
    el: root,
    setPages(next) {
      const uniq = [...new Set(next.filter((n) => n > 0))].sort((a, b) => a - b);
      const same =
        uniq.length === pages.length && uniq.every((n, i) => n === pages[i]);
      pages = uniq;
      if (uniq.length) jumpInput.max = String(uniq[uniq.length - 1]);
      if (!same) render();
      else {
        root.classList.toggle("is-empty", pages.length === 0);
        updateMeta();
        scrollActiveIntoView();
      }
    },
    setActive(pageNo) {
      const n = Number(pageNo) || 0;
      if (n === activePage) {
        updateMeta();
        return;
      }
      buttons.get(activePage)?.classList.remove("is-active");
      activePage = n;
      buttons.get(activePage)?.classList.add("is-active");
      updateMeta();
      scrollActiveIntoView();
    },
    destroy() {
      root.remove();
      buttons.clear();
      pages = [];
    },
  };
}
