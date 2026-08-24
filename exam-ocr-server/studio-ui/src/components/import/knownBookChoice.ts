// 同一本书重导的选择块（MinerU 线）。
//
// 选完 PDF 时 pdf-probe 会顺带回一个 known_books 候选列表（按原始 PDF 指纹比对
// 历史导入任务，不需要先花一次云解析）。命中就在导入表单里让用户明确决定：
// 重新生成 / 继承上次改好的页 / 直接打开上次那个任务。

import { el } from "../../util/dom";

export type ImportMode = "regenerate" | "inherit" | "open_previous";

export interface KnownBookCandidate {
  job_id: string;
  label?: string;
  status?: string;
  edition_id?: string;
  created_at?: string;
  /** same_file / same_body_pages / similar_body_pages，见 MATCH_TEXT */
  match?: string;
  /** 正文取样页里「文本完全一致」的页数 */
  shared_pages?: number;
  /** 正文取样页里「读起来几乎一样」的页数（不同导出/扫描件） */
  similar_pages?: number;
  page_count?: number;
  improved_pages?: number;
  fingerprint_hash?: string;
}

export interface KnownBooksProbe {
  candidates?: KnownBookCandidate[];
  best?: KnownBookCandidate | null;
}

export interface KnownBookChoiceHandle {
  /** 挂进导入表单 grid 的整行字段 */
  el: HTMLElement;
  /** 用 probe 结果刷新；无候选则自动隐藏 */
  update(known: KnownBooksProbe | null | undefined): void;
  clear(): void;
  /** 无候选时恒为 regenerate */
  mode(): ImportMode;
  /** inherit / open_previous 的来源任务 id */
  sourceJobId(): string;
  /** 一句话摘要，写进面板 msg */
  summary(): string;
}

/** 判定依据都取自原始 PDF 的文本层（未经规则/AI 改写），且跳过封面等前 3 页。 */
function matchText(c: KnownBookCandidate): string {
  if (c.match === "same_file") return "同一份 PDF 文件";
  if (c.match === "same_body_pages") return `正文有 ${c.shared_pages || 0} 页文本完全一致`;
  if (c.match === "similar_body_pages") return `正文有 ${c.similar_pages || 0} 页几乎一致（不同导出件）`;
  return "疑似同一本";
}

function describe(c: KnownBookCandidate): string {
  const bits = [c.label || c.job_id.slice(0, 8)];
  if (c.page_count) bits.push(`${c.page_count} 页`);
  if (c.improved_pages) bits.push(`已改好 ${c.improved_pages} 页`);
  if (c.created_at) bits.push(String(c.created_at).slice(0, 10));
  bits.push(matchText(c));
  return bits.join(" · ");
}

export function mountKnownBookChoice(opts: {
  onOpenPrevious?: (jobId: string) => void;
} = {}): KnownBookChoiceHandle {
  const root = el("div", "import-field import-field--wide import-known-book hidden");
  const head = el("span", "import-field-label", "检测到这本书已导入过");
  const desc = el("p", "import-known-book-desc muted", "");
  const basis = el(
    "p",
    "import-known-book-desc muted",
    "判定依据：原始 PDF 的文本层（未经规则/AI 改写），跳过封面等前 3 页后取样多页正文比对",
  );

  const picker = document.createElement("select");
  picker.className = "import-input import-known-book-picker hidden";

  const modes = el("div", "import-known-book-modes");
  const name = `import-known-book-${Math.random().toString(36).slice(2, 8)}`;
  const radios = new Map<ImportMode, HTMLInputElement>();
  const addMode = (mode: ImportMode, title: string, hint: string): void => {
    const row = el("label", "import-known-book-mode");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.name = name;
    radio.value = mode;
    const text = el("span", "import-known-book-mode-text");
    text.append(
      el("span", "import-known-book-mode-title", title),
      el("span", "import-known-book-mode-hint muted", hint),
    );
    row.append(radio, text);
    modes.append(row);
    radios.set(mode, radio);
  };

  addMode(
    "inherit",
    "继承上次结果（推荐）",
    "重跑 MinerU 拿新解析，解析完把上次已改好的页整页搬回来；这些页不再进 escalate 清单",
  );
  addMode("regenerate", "重新生成", "完全按新解析走，忽略上次的所有修订");
  addMode("open_previous", "打开上次的导入任务", "不重跑、不花云解析额度，直接回到上次那本书继续改");

  const openBtn = el("button", "btn btn-secondary import-known-book-open hidden", "打开上次的任务");
  (openBtn as HTMLButtonElement).type = "button";

  root.append(head, desc, basis, picker, modes, openBtn);

  let candidates: KnownBookCandidate[] = [];

  const current = (): KnownBookCandidate | null => {
    if (!candidates.length) return null;
    const wanted = picker.value;
    return candidates.find((c) => c.job_id === wanted) || candidates[0];
  };

  const modeOf = (): ImportMode => {
    if (!candidates.length) return "regenerate";
    for (const [mode, radio] of radios) if (radio.checked) return mode;
    return "inherit";
  };

  const syncOpenBtn = (): void => {
    openBtn.classList.toggle("hidden", modeOf() !== "open_previous");
  };

  for (const radio of radios.values()) radio.onchange = syncOpenBtn;

  picker.onchange = () => {
    const c = current();
    if (c) desc.textContent = describe(c);
  };

  (openBtn as HTMLButtonElement).onclick = () => {
    const c = current();
    if (c && opts.onOpenPrevious) opts.onOpenPrevious(c.job_id);
  };

  const clear = (): void => {
    candidates = [];
    picker.innerHTML = "";
    picker.classList.add("hidden");
    desc.textContent = "";
    root.classList.add("hidden");
    openBtn.classList.add("hidden");
  };

  const update = (known: KnownBooksProbe | null | undefined): void => {
    const list = (known?.candidates || []).filter((c) => c && c.job_id);
    if (!list.length) {
      clear();
      return;
    }
    candidates = list;
    picker.innerHTML = "";
    for (const c of list) {
      const opt = document.createElement("option");
      opt.value = c.job_id;
      opt.textContent = describe(c);
      picker.append(opt);
    }
    picker.value = list[0].job_id;
    picker.classList.toggle("hidden", list.length < 2);
    desc.textContent = describe(list[0]);
    const inherit = radios.get("inherit");
    if (inherit) inherit.checked = true;
    root.classList.remove("hidden");
    syncOpenBtn();
  };

  clear();

  return {
    el: root,
    update,
    clear,
    mode: modeOf,
    sourceJobId: () => (modeOf() === "regenerate" ? "" : current()?.job_id || ""),
    summary: () => {
      const c = current();
      if (!c) return "";
      const mode = modeOf();
      if (mode === "inherit") return `将继承上次任务 ${c.job_id.slice(0, 8)} 已改好的页`;
      if (mode === "open_previous") return `打开上次任务 ${c.job_id.slice(0, 8)}`;
      return "按新解析重新生成，不继承上次修订";
    },
  };
}
