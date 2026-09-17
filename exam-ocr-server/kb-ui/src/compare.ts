import { kb } from "./api";
import { applyDocumentTheme, loadSurface } from "./surfaces";
import { el } from "./util/dom";
import { sanitizeHtml } from "./util/html-sanitize";

export type ComparePane = "old" | "ir" | "image";

export type CompareMode = {
  id: string;
  label: string;
  left: ComparePane;
  right: ComparePane;
  left_label: string;
  right_label: string;
};

const DEFAULT_MODES: CompareMode[] = [
  { id: "old-ir", label: "旧|IR", left: "old", right: "ir", left_label: "旧稿", right_label: "中间表示" },
  { id: "ir-image", label: "IR|原图", left: "ir", right: "image", left_label: "中间表示", right_label: "原图" },
  { id: "old-image", label: "旧|原图", left: "old", right: "image", left_label: "旧稿", right_label: "原图" },
];

function asPane(value: unknown, fallback: ComparePane): ComparePane {
  return value === "old" || value === "ir" || value === "image" ? value : fallback;
}

function parseModes(raw: unknown): CompareMode[] {
  if (!Array.isArray(raw)) return DEFAULT_MODES;
  const out: CompareMode[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const item = row as Record<string, unknown>;
    const id = String(item.id || "").trim();
    if (!id) continue;
    out.push({
      id,
      label: String(item.label || id),
      left: asPane(item.left, "old"),
      right: asPane(item.right, "image"),
      left_label: String(item.left_label || item.label || id),
      right_label: String(item.right_label || ""),
    });
  }
  return out.length ? out : DEFAULT_MODES;
}

export type KbCompare = {
  setEnabled: (on: boolean) => void;
  isEnabled: () => boolean;
  bindBook: (bookId: string | null) => void;
  wrapIfNeeded: (pageNo: number) => void;
  getMode: () => CompareMode;
  rightHost: (pageNo: number) => HTMLElement | null;
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
  let modes = DEFAULT_MODES;
  let modeId = "old-ir";

  const toolbar = el("div", "kb-preview-head");
  const toggle = el("button", "btn btn-secondary", "对照原图") as HTMLButtonElement;
  toggle.type = "button";
  const modeSelect = document.createElement("select");
  modeSelect.className = "kb-compare-mode";
  modeSelect.hidden = true;
  const refresh = el("button", "btn btn-secondary", "刷新当前页") as HTMLButtonElement;
  refresh.type = "button";
  const hint = el("span", "muted kb-preview-hint", "");
  toolbar.append(toggle, modeSelect, refresh, hint);
  opts.previewWrap.insertBefore(toolbar, opts.previewPages);

  const legend = el("div", "kb-compare-legend");
  legend.append(el("div", "kb-compare-legend-cell", "MinerU 文本"), el("div", "kb-compare-legend-cell", "原始页图"));

  function currentMode(): CompareMode {
    return modes.find((row) => row.id === modeId) || modes[0] || DEFAULT_MODES[0];
  }

  function fillModeSelect(): void {
    modeSelect.replaceChildren();
    for (const row of modes) {
      const opt = document.createElement("option");
      opt.value = row.id;
      opt.textContent = row.label;
      if (row.id === modeId) opt.selected = true;
      modeSelect.appendChild(opt);
    }
  }

  async function paintLegend(): Promise<void> {
    const mode = currentMode();
    legend.replaceChildren(
      el("div", "kb-compare-legend-cell", mode.left_label),
      el("div", "kb-compare-legend-cell", mode.right_label),
    );
    try {
      const painted = await kb.surfacePaint("compare", { kind: "legend", mode: mode.id });
      if (painted.html) legend.innerHTML = sanitizeHtml(painted.html);
    } catch {
      /* keep local labels */
    }
  }

  void (async () => {
    try {
      const slot = await loadSurface("compare");
      applyDocumentTheme("compare", slot.css);
      const side = String(slot.profile.image_side || "right");
      if (side === "left" || side === "right") imageSide = side;
      if (typeof slot.profile.image_tag === "string" && slot.profile.image_tag) {
        imageTagTmpl = slot.profile.image_tag;
      }
      modes = parseModes(slot.profile.modes);
      const def = String(slot.profile.default_mode || "old-image");
      if (modes.some((row) => row.id === def)) modeId = def;
      fillModeSelect();
      await paintLegend();
    } catch {
      fillModeSelect();
    }
  })();

  function updateHint(): void {
    refresh.disabled = !bookId;
    toggle.disabled = !bookId;
    toggle.textContent = enabled ? "退出对照" : "对照原图";
    toggle.classList.toggle("is-active", enabled);
    modeSelect.hidden = !enabled;
    if (!bookId) hint.textContent = "从左侧打开一本书";
    else if (enabled) hint.textContent = `同页左右并排 · ${currentMode().label}`;
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

  function mdCell(pageNo: number, side: "left" | "right"): HTMLElement {
    const cell = el("div", "kb-compare-cell kb-compare-cell--html");
    const host = el("div", "kb-page-body kb-compare-md is-unloaded");
    host.dataset.compareSide = side;
    host.dataset.pageNo = String(pageNo);
    cell.appendChild(host);
    return cell;
  }

  function paneCell(pageNo: number, pane: ComparePane, side: "left" | "right"): HTMLElement {
    if (pane === "image") return imageCell(pageNo);
    return mdCell(pageNo, side);
  }

  function wrapBlock(block: HTMLElement): void {
    if (!enabled || block.closest(".kb-compare-row")) return;
    const pageNo = Number(block.dataset.pageNo || 0);
    if (pageNo <= 0) return;
    const mode = currentMode();
    const row = el("div", "kb-compare-row");
    row.dataset.comparePage = String(pageNo);
    const leftCell =
      mode.left === "image" ? imageCell(pageNo) : el("div", "kb-compare-cell kb-compare-cell--html");
    const rightCell = paneCell(pageNo, mode.right, "right");
    block.replaceWith(row);
    if (mode.left !== "image") leftCell.appendChild(block);
    if (imageSide === "left") row.append(rightCell, leftCell);
    else row.append(leftCell, rightCell);
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

  async function refillLoaded(): Promise<void> {
    const pages = new Set<number>();
    for (const node of opts.previewPages.querySelectorAll<HTMLElement>("[data-page-no]")) {
      const n = Number(node.dataset.pageNo || 0);
      if (n > 0) pages.add(n);
    }
    for (const pageNo of [...pages].sort((a, b) => a - b)) {
      const body = opts.previewPages.querySelector<HTMLElement>(
        `.kb-page-block[data-page-no="${pageNo}"] .kb-page-body, .kb-compare-row[data-compare-page="${pageNo}"] .kb-page-body`,
      );
      if (body && !body.classList.contains("is-unloaded")) {
        await opts.onRefreshPage(pageNo);
      }
    }
  }

  function enable(): void {
    if (enabled || !bookId) return;
    enabled = true;
    opts.previewWrap.classList.add("is-compare");
    if (!legend.isConnected) opts.previewWrap.insertBefore(legend, opts.previewPages);
    void paintLegend();
    wrapAll();
    updateHint();
    void refillLoaded();
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
  modeSelect.onchange = () => {
    modeId = modeSelect.value || "old-image";
    void paintLegend();
    if (enabled) {
      unwrapAll();
      wrapAll();
      updateHint();
      void refillLoaded();
    }
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

  fillModeSelect();
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
    getMode: () => currentMode(),
    rightHost: (pageNo) =>
      opts.previewPages.querySelector<HTMLElement>(
        `.kb-compare-row[data-compare-page="${pageNo}"] .kb-compare-md[data-compare-side="right"]`,
      ),
    toolbar,
  };
}
