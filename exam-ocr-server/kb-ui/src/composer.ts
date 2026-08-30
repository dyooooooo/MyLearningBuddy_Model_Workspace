import { agent, type ChatAttachment, type ChatImage, type ChatMode } from "./api";
import {
  closeModelPicker,
  defaultParams,
  openModelPicker,
  readModelId,
  readModelParams,
  saveModelState,
  statusLabel,
  type CatalogModel,
  type ModelParam,
} from "./modelPicker";
import { chevron, closePopover, openPopover } from "./popover";
import { el } from "./util/dom";

const MODE_KEY = "kb.dock.mode";
const MAX_ITEMS = 8;
const MAX_BYTES = 6 * 1024 * 1024;

const MODES: { id: ChatMode; icon: string; label: string; hint: string }[] = [
  { id: "agent", icon: "∞", label: "Agent", hint: "可改工作区里的书、页和规矩" },
  { id: "plan", icon: "☰", label: "Plan", hint: "先调查，写出计划，这一轮不改文件" },
  { id: "ask", icon: "?", label: "Ask", hint: "只读问答，不改任何文件" },
];

const HINTS: Record<ChatMode, string> = {
  agent: "给 Agent 一条指令…",
  plan: "描述要规划的事…",
  ask: "问一句（只读，不改文件）…",
};

export type ComposerSubmit = {
  message: string;
  mode: ChatMode;
  model: string;
  model_params: ModelParam[];
  images: ChatImage[];
  files: { data: string; name: string; mime_type: string }[];
  attachments: ChatAttachment[];
};

type Pending = { id: string; file: File; kind: "image" | "file"; preview?: string };
type FileRow = { path: string; name: string };

export function mountComposer(
  form: HTMLFormElement,
  opts: {
    onSubmit: (body: ComposerSubmit) => Promise<void>;
    isBusy: () => boolean;
    onStop?: () => Promise<void>;
    onReview?: () => void;
  },
): {
    setBusy: (on: boolean) => void;
    setFiles: (rows: FileRow[]) => void;
    setDraft: (text: string, select?: boolean) => void;
    submit: () => void;
    focus: () => void;
  } {
  form.className = "kb-dock-composer";
  const box = el("div", "kb-dock-box");
  const ctx = el("div", "kb-dock-ctx");
  const filesBtn = el("button", "kb-dock-files") as HTMLButtonElement;
  filesBtn.type = "button";
  filesBtn.title = "工作区里的教材文件";
  const ctxSpace = el("span", "kb-dock-toolbar-space");
  const reviewBtn = el("button", "kb-dock-review", "Review") as HTMLButtonElement;
  reviewBtn.type = "button";
  reviewBtn.title = "打开过往轨迹";
  ctx.append(filesBtn, ctxSpace, reviewBtn);

  const chips = el("div", "kb-dock-attach-row hidden");
  const ta = document.createElement("textarea");
  ta.className = "kb-dock-input";
  ta.rows = 1;
  const foot = el("div", "kb-dock-toolbar");
  const modeBtn = pillBtn("kb-dock-mode");
  modeBtn.setAttribute("aria-label", "对话模式");
  const modelBtn = pillBtn("kb-dock-model");
  modelBtn.setAttribute("aria-label", "模型");
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.multiple = true;
  fileInput.accept = "image/*,.pdf,.md,.txt,.json";
  fileInput.className = "hidden";
  const attachBtn = el("button", "kb-dock-icon") as HTMLButtonElement;
  attachBtn.type = "button";
  attachBtn.title = "添加附件（也可粘贴图片）";
  attachBtn.innerHTML = paperclipSvg();
  const send = el("button", "kb-dock-send") as HTMLButtonElement;
  send.type = "submit";
  send.title = "发送";
  send.innerHTML = sendSvg();
  const stop = el("button", "kb-dock-stop hidden") as HTMLButtonElement;
  stop.type = "button";
  stop.title = "停止";
  stop.innerHTML = stopSvg();
  stop.onclick = () => {
    void opts.onStop?.();
  };
  foot.append(modeBtn, modelBtn, el("span", "kb-dock-toolbar-space"), attachBtn, fileInput, send, stop);
  box.append(ctx, chips, ta, foot);
  form.replaceChildren(box);

  const pending: Pending[] = [];
  let files: FileRow[] = [];
  let models: CatalogModel[] = [];
  let mode = readMode();
  let modelId = readModelId();
  let modelParams: ModelParam[] = readModelParams();
  ta.placeholder = HINTS[mode];
  paintMode();
  paintModel();
  paintFiles();

  void agent
    .models()
    .then((out) => {
      models = out.models || [];
      const auto = models.find((m) => m.id === "auto-smart") || models.find((m) => m.id === "default" || m.id === "auto");
      const preferred = out.default || "grok-4.6";
      if (!modelId || modelId.startsWith("composer")) modelId = preferred;
      if (modelId && !models.some((m) => m.id === modelId)) modelId = preferred || auto?.id || modelId;
      const row = models.find((m) => m.id === modelId);
      if (!modelParams.length) modelParams = defaultParams(row);
      if (modelId) saveModelState(modelId, modelParams);
      paintModel();
    })
    .catch(() => {
      /* keep fallback */
    });

  function paintMode(): void {
    const row = MODES.find((m) => m.id === mode) || MODES[0];
    modeBtn.replaceChildren();
    modeBtn.append(el("span", "kb-pill-ico", row.icon), el("span", "", row.label));
    modeBtn.insertAdjacentHTML("beforeend", chevron());
    modeBtn.title = `${row.label} · ${row.hint}`;
    ta.placeholder = HINTS[mode];
  }

  function paintModel(): void {
    const row = models.find((m) => m.id === modelId);
    const label = statusLabel(row, modelParams);
    modelBtn.replaceChildren();
    modelBtn.append(el("span", "", label));
    modelBtn.insertAdjacentHTML("beforeend", chevron());
    modelBtn.title = row ? `${row.label} · ${label}` : "Model";
  }

  function paintFiles(): void {
    const n = files.length;
    filesBtn.replaceChildren();
    filesBtn.insertAdjacentHTML("afterbegin", chevron("right"));
    filesBtn.append(document.createTextNode(n ? `${n} 个文件` : "工作区"));
    filesBtn.classList.toggle("is-empty", n === 0);
  }

  function paintChips(): void {
    chips.replaceChildren();
    chips.classList.toggle("hidden", pending.length === 0);
    pending.forEach((item, i) => {
      const chip = el("span", "kb-dock-chip");
      if (item.kind === "image" && item.preview) {
        const img = document.createElement("img");
        img.className = "kb-dock-chip-img";
        img.src = item.preview;
        img.alt = item.file.name;
        chip.append(img);
      }
      chip.append(el("span", "kb-dock-chip-name", item.file.name || "图片"));
      const rm = el("button", "kb-dock-chip-rm", "×") as HTMLButtonElement;
      rm.type = "button";
      rm.onclick = () => {
        if (item.preview) URL.revokeObjectURL(item.preview);
        pending.splice(i, 1);
        paintChips();
      };
      chip.append(rm);
      chips.append(chip);
    });
  }

  function addFiles(list: File[]): void {
    for (const file of list) {
      if (pending.length >= MAX_ITEMS) break;
      if (file.size > MAX_BYTES) {
        window.alert(`${file.name} 超过 6MB`);
        continue;
      }
      const kind = file.type.startsWith("image/") ? "image" : "file";
      pending.push({
        id: `${Date.now()}-${pending.length}`,
        file,
        kind,
        preview: kind === "image" ? URL.createObjectURL(file) : undefined,
      });
    }
    paintChips();
  }

  function clearPending(): void {
    for (const item of pending) {
      if (item.preview) URL.revokeObjectURL(item.preview);
    }
    pending.length = 0;
    paintChips();
  }

  function grow(): void {
    ta.style.height = "auto";
    ta.style.height = `${Math.min(Math.max(ta.scrollHeight, 28), 160)}px`;
  }

  modeBtn.onclick = () => {
    closeModelPicker();
    openPopover(
      modeBtn,
      (close) => {
        const menu = el("div", "kb-pop-list");
        for (const row of MODES) {
          const btn = el("button", `kb-pop-item${row.id === mode ? " is-on" : ""}`) as HTMLButtonElement;
          btn.type = "button";
          const top = el("span", "kb-pop-row");
          top.append(el("span", "kb-pill-ico", row.icon), el("strong", "", row.label));
          btn.append(top, el("span", "muted", row.hint));
          btn.onclick = () => {
            mode = row.id;
            writeMode(mode);
            paintMode();
            close();
          };
          menu.append(btn);
        }
        return menu;
      },
      { width: 280 },
    );
  };

  modelBtn.onclick = () => {
    closePopover();
    openModelPicker(modelBtn, models, { modelId, params: modelParams }, (next) => {
      modelId = next.modelId;
      modelParams = next.params;
      paintModel();
    });
  };

  filesBtn.onclick = () => {
    closeModelPicker();
    openPopover(
      filesBtn,
      () => {
        const list = el("div", "kb-pop-list");
        if (!files.length) {
          list.append(el("p", "muted kb-pop-empty", "工作区还没有 PDF / Markdown"));
          return list;
        }
        for (const row of files.slice(0, 80)) {
          const item = el("div", "kb-pop-file");
          item.append(el("code", "", row.name), el("span", "muted", row.path));
          list.append(item);
        }
        return list;
      },
      { width: 300 },
    );
  };

  reviewBtn.onclick = () => {
    closePopover();
    closeModelPicker();
    opts.onReview?.();
  };
  attachBtn.onclick = () => fileInput.click();
  fileInput.onchange = () => {
    addFiles([...(fileInput.files || [])]);
    fileInput.value = "";
  };
  ta.addEventListener("input", grow);
  ta.addEventListener("paste", (ev) => {
    const items = ev.clipboardData?.items;
    if (!items) return;
    const got: File[] = [];
    for (const it of items) {
      if (it.kind === "file" && it.type.startsWith("image/")) {
        const f = it.getAsFile();
        if (f) got.push(f);
      }
    }
    if (got.length) {
      ev.preventDefault();
      addFiles(got);
    }
  });
  box.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    box.classList.add("is-drop");
  });
  box.addEventListener("dragleave", () => box.classList.remove("is-drop"));
  box.addEventListener("drop", (ev) => {
    ev.preventDefault();
    box.classList.remove("is-drop");
    addFiles([...(ev.dataTransfer?.files || [])]);
  });
  ta.onkeydown = (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      form.requestSubmit();
    }
  };

  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const message = ta.value.trim();
    if ((!message && !pending.length) || send.disabled || opts.isBusy()) return;
    send.disabled = true;
    try {
      const images: ChatImage[] = [];
      const outFiles: ComposerSubmit["files"] = [];
      const attachments: ChatAttachment[] = [];
      for (const item of pending) {
        const data = await fileToB64(item.file);
        const mime = item.file.type || (item.kind === "image" ? "image/png" : "application/octet-stream");
        attachments.push({ kind: item.kind, name: item.file.name });
        if (item.kind === "image") images.push({ data, mime_type: mime, name: item.file.name });
        else outFiles.push({ data, name: item.file.name, mime_type: mime });
      }
      ta.value = "";
      grow();
      clearPending();
      await opts.onSubmit({
        message,
        mode,
        model: modelId,
        model_params: modelParams,
        images,
        files: outFiles,
        attachments,
      });
    } catch (e) {
      window.alert((e as Error).message || "发送失败");
    } finally {
      send.disabled = opts.isBusy();
      ta.focus();
    }
  };

  grow();
  return {
    setBusy(on: boolean) {
      send.disabled = on;
      send.classList.toggle("hidden", on);
      stop.classList.toggle("hidden", !on);
      box.classList.toggle("is-busy", on);
    },
    setFiles(rows: FileRow[]) {
      files = rows;
      paintFiles();
    },
    setDraft(text: string, select = true) {
      ta.value = text;
      grow();
      ta.focus();
      if (select) ta.setSelectionRange(0, ta.value.length);
    },
    submit() {
      form.requestSubmit();
    },
    focus() {
      ta.focus();
    },
  };
}

function pillBtn(extra: string): HTMLButtonElement {
  const btn = el("button", `kb-pill ${extra}`) as HTMLButtonElement;
  btn.type = "button";
  return btn;
}

function paperclipSvg(): string {
  return `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M21.4 11.6 12 21a5 5 0 0 1-7.1-7.1l11-11a3.2 3.2 0 1 1 4.5 4.5L9.3 18.5a1.4 1.4 0 0 1-2-2l9.2-9.2"/></svg>`;
}

function sendSvg(): string {
  return `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 19V5M6 11l6-6 6 6"/></svg>`;
}

function stopSvg(): string {
  return `<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>`;
}

function fileToB64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result || "");
      const i = s.indexOf(",");
      resolve(i >= 0 ? s.slice(i + 1) : s);
    };
    r.onerror = () => reject(r.error || new Error("read failed"));
    r.readAsDataURL(file);
  });
}

function readMode(): ChatMode {
  try {
    const v = window.localStorage.getItem(MODE_KEY);
    if (v === "plan" || v === "ask" || v === "agent") return v;
  } catch {
    /* ignore */
  }
  return "agent";
}

function writeMode(mode: ChatMode): void {
  try {
    window.localStorage.setItem(MODE_KEY, mode);
  } catch {
    /* ignore */
  }
}

