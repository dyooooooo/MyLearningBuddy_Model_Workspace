import { kb } from "./api";
import { stampBlocks } from "./pageFocus";
import { paneStillOwned } from "./paneOwn";
import { fillIrPage } from "./renderIr";
import { fillShadow, setShadowText } from "./surfaces";
import { renderMarkdown } from "./util/dom";

/** 对照格子里能放的两种文本：老线（冻结参照）与中间表示。 */
export type PaneSrc = "old" | "ir";

export { paneStillOwned } from "./paneOwn";

/**
 * 把一页正文画进一个影子宿主。两个库的预览、以及对照的左右两格都走这一份，
 * 免得「教材库能读中间表示、结构化库读不了老线」这种半边接线。
 */
export async function fillPagePane(
  host: HTMLElement,
  bookId: string,
  pageNo: number,
  src: PaneSrc,
): Promise<void> {
  if (!bookId || pageNo <= 0) return;
  const expectedPage = panePageNo(host) || pageNo;
  setShadowText(host, "加载中…");
  try {
    if (src === "ir") {
      const out = await kb.irBlocks(bookId, pageNo);
      if (!paneStillOwned(host, pageNo, panePageNo(host) || expectedPage)) return;
      await fillIrPage(host, out.page || {}, bookId);
      if (!paneStillOwned(host, pageNo, panePageNo(host) || expectedPage)) return;
      host.classList.remove("is-unloaded", "is-error");
      return;
    }
    const out = await kb.pageMarkdown(bookId, pageNo);
    if (!paneStillOwned(host, pageNo, panePageNo(host) || expectedPage)) return;
    if ("ok" in out && out.ok === false) {
      setShadowText(host, ("reason" in out && out.reason) || "本页读不开");
      host.classList.remove("is-unloaded");
      host.classList.add("is-error");
      return;
    }
    const html = renderMarkdown(out.prepared || out.markdown || "（本页无文本）");
    await fillShadow(host, "page-body", html);
    if (!paneStillOwned(host, pageNo, panePageNo(host) || expectedPage)) return;
    const inner = host.shadowRoot?.querySelector(".kb-surface-inner");
    if (inner) stampBlocks(inner);
    host.classList.remove("is-unloaded", "is-error");
  } catch (e) {
    if (!paneStillOwned(host, pageNo, panePageNo(host) || expectedPage)) return;
    const fallback = src === "ir" ? "中间表示未生成" : "加载失败";
    setShadowText(host, `${fallback}：${(e as Error).message}`);
    host.classList.add("is-unloaded", "is-error");
  }
}

/** 读页面壳上的页号：对照格子和整页壳都把页号写在 data-page-no 上。 */
export function panePageNo(host: HTMLElement): number {
  const attr = host.closest("[data-page-no]")?.getAttribute("data-page-no");
  return Number(attr || host.dataset.pageNo || 0);
}
