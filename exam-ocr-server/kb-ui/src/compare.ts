import { kb } from "./api";
import { applyDocumentTheme, loadSurface } from "./surfaces";
import { el } from "./util/dom";
import { sanitizeHtml } from "./util/html-sanitize";

export type KbCompare = {
  setEnabled: (on: boolean) => void;
  isEnabled: () => boolean;
  bindBook: (bookId: string | null) => void;
  wrapIfNeeded: (pageNo: number) => void;
  toolbar: HTMLElement;
};

export function mountKbCompare(opts: {
  previewWrap: HTMLElement;
  previewPages: HTMLElement;
  imageSrc: (bookId: string, pageNo: number) => string;
  getActivePage: () => number;
  onRefreshPage: (pageNo: number) => Promise<void>;
}): KbCompare {
  let enabled = false;
  let bookId: string | null = null;
  let imageSide = "right";
  let imageTagTmpl = "第 {page} 页 · 原图";

  const toolbar = el("div", "kb-preview-head");
  const toggle = el("button", "btn btn-secondary", "对照原图") as HTMLButtonElement;
  toggle.type = "button";
  const refresh = el("button", "btn btn-secondary", "刷新当前页") as HTMLButtonElement;
  refresh.type = "button";
  const hint = el("span", "muted kb-preview-hint", "");
  toolbar.append(toggle, refresh, hint);
  opts.previewWrap.insertBefore(toolbar, opts.previewPages);

  const legend = el("div", "kb-compare-legend");
  legend.append(el("div", "kb-compare-legend-cell", "MinerU 文本"), el("div", "kb-compare-legend-cell", "原始页图"));
  void (async () => {
    try {
      const slot = await loadSurface("compare");
      applyDocumentTheme("compare", slot.css);
      const side = String(slot.profile.image_side || "right");
      if (side === "left" || side === "right") imageSide = side;
      if (typeof slot.profile.image_tag === "string" && slot.profile.image_tag) {
        imageTagTmpl = slot.profile.image_tag;
      }
      const painted = await kb.surfacePaint("compare", { kind: "legend" });
      if (painted.html) legend.innerHTML = sanitizeHtml(painted.html);
    } catch {
      /* keep host defaults */
    }
  })();

  function updateHint(): void {
    refresh.disabled = !bookId;
    toggle.disabled = !bookId;
    toggle.textContent = enabled ? "退出对照" : "对照原图";
    toggle.classList.toggle("is-active", enabled);
    if (!bookId) hint.textContent = "从左侧打开一本书";
    else if (enabled) hint.textContent = "同页左右并排";
    else hint.textContent = "可对照原图，或刷新当前页";
  }

  function imageCell(pageNo: number): HTMLElement {
    const cell = el("div", "kb-compare-cell kb-compare-cell--image");
    cell.append(el("div", "kb-compare-img-tag", imageTagTmpl.replace("{page}", String(pageNo))));
    const body = el("div", "kb-compare-img-body");
    if (!bookId) {
      body.textContent = "未打开书";
    } else {
      const img = document.createElement("img");
      img.className = "kb-compare-img";
      img.alt = `第 ${pageNo} 页`;
      img.src = opts.imageSrc(bookId, pageNo);
      body.appendChild(img);
    }
    cell.appendChild(body);
    return cell;
  }

  function wrapBlock(block: HTMLElement): void {
    if (!enabled || block.closest(".kb-compare-row")) return;
    const pageNo = Number(block.dataset.pageNo || 0);
    if (pageNo <= 0) return;
    const row = el("div", "kb-compare-row");
    row.dataset.comparePage = String(pageNo);
    const htmlCell = el("div", "kb-compare-cell kb-compare-cell--html");
    block.replaceWith(row);
    htmlCell.appendChild(block);
    if (imageSide === "left") row.append(imageCell(pageNo), htmlCell);
    else row.append(htmlCell, imageCell(pageNo));
  }

  function wrapAll(): void {
    for (const block of [
      ...opts.previewPages.querySelectorAll<HTMLElement>(":scope > .kb-page-block[data-page-no]"),
    ]) {
      wrapBlock(block);
    }
  }

  function unwrapAll(): void {
    for (const row of [...opts.previewPages.querySelectorAll<HTMLElement>(":scope > .kb-compare-row")]) {
      const block = row.querySelector<HTMLElement>(":scope > .kb-compare-cell--html > .kb-page-block");
      if (block) row.replaceWith(block);
      else row.remove();
    }
  }

  function enable(): void {
    if (enabled || !bookId) return;
    enabled = true;
    opts.previewWrap.classList.add("is-compare");
    if (!legend.isConnected) opts.previewWrap.insertBefore(legend, opts.previewPages);
    wrapAll();
    updateHint();
  }

  function disable(): void {
    if (!enabled) return;
    enabled = false;
    opts.previewWrap.classList.remove("is-compare");
    legend.remove();
    unwrapAll();
    updateHint();
  }

  toggle.onclick = () => {
    if (enabled) disable();
    else enable();
  };
  refresh.onclick = async () => {
    const pageNo = opts.getActivePage();
    if (pageNo <= 0) {
      hint.textContent = "请先滚到要刷新的页";
      return;
    }
    hint.textContent = `正在刷新第 ${pageNo} 页…`;
    try {
      await opts.onRefreshPage(pageNo);
      hint.textContent = `已刷新第 ${pageNo} 页`;
    } catch (e) {
      hint.textContent = `刷新失败：${(e as Error).message}`;
    }
  };

  updateHint();
  return {
    setEnabled: (on) => {
      if (on) enable();
      else disable();
    },
    isEnabled: () => enabled,
    bindBook: (id) => {
      bookId = (id || "").trim() || null;
      if (enabled) {
        unwrapAll();
        if (bookId) wrapAll();
        else disable();
      }
      updateHint();
    },
    wrapIfNeeded: (pageNo) => {
      if (!enabled || pageNo <= 0) return;
      const block =
        opts.previewPages.querySelector<HTMLElement>(
          `:scope > .kb-page-block[data-page-no="${pageNo}"]`,
        ) ||
        opts.previewPages.querySelector<HTMLElement>(
          `.kb-compare-row[data-compare-page="${pageNo}"] .kb-page-block`,
        );
      if (block) wrapBlock(block);
    },
    toolbar,
  };
}
