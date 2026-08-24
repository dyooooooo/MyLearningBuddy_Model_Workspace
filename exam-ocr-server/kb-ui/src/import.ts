import { kb, type KbBook } from "./api";
import { mountKbSettings } from "./settings";
import { el } from "./util/dom";

function field(label: string, control: HTMLElement): HTMLElement {
  const wrap = el("label", "kb-field");
  wrap.append(el("span", "kb-field-label", label), control);
  return wrap;
}

function selectOpts(host: HTMLSelectElement, pairs: readonly [string, string][]): void {
  for (const [value, text] of pairs) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    host.appendChild(o);
  }
}

export function mountKbImport(
  host: HTMLElement,
  opts: { onQueued: (book: KbBook) => void },
): void {
  host.className = "kb-import hidden";
  host.append(
    el("h1", "kb-title", "导入教材"),
    el("p", "muted", "把原书 PDF 交给 MinerU 识别，只给人核对；判题仍读原书。"),
  );

  const work = el("div", "kb-import-work");
  const drop = el("div", "kb-drop");
  drop.tabIndex = 0;
  const dropHint = el("div", "kb-drop-hint", "把 PDF 拖到这里，或点击选择");
  const dropName = el("div", "kb-drop-name muted", "尚未选择文件");
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "application/pdf";
  fileInput.className = "kb-file-hidden";
  drop.append(dropHint, dropName, fileInput);

  function setFile(file: File | null): void {
    if (!file) {
      fileInput.value = "";
      dropName.textContent = "尚未选择文件";
      drop.classList.remove("has-file");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      dropName.textContent = "只接受 PDF";
      return;
    }
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    dropName.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB`;
    drop.classList.add("has-file");
    if (!labelInput.value.trim()) labelInput.value = file.name.replace(/\.pdf$/i, "");
  }

  drop.onclick = () => fileInput.click();
  fileInput.onclick = (ev) => ev.stopPropagation();
  fileInput.onchange = () => setFile(fileInput.files?.[0] || null);
  drop.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    drop.classList.add("is-over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop.addEventListener("drop", (ev) => {
    ev.preventDefault();
    drop.classList.remove("is-over");
    setFile(ev.dataTransfer?.files?.[0] || null);
  });

  const labelInput = document.createElement("input");
  labelInput.placeholder = "如 普通高中教科书·数学必修第一册";
  const hintInput = document.createElement("input");
  hintInput.placeholder = "给判题用的书名，不要带 .pdf";
  const subject = document.createElement("select");
  selectOpts(subject, [
    ["math", "数学"],
    ["chinese", "语文"],
    ["english", "英语"],
  ]);
  const grade = document.createElement("select");
  selectOpts(grade, [
    ["hs", "高中"],
    ["ms", "初中"],
    ["ps", "小学"],
  ]);

  const grid = el("div", "kb-import-grid");
  grid.append(
    field("显示名", labelInput),
    field("书名提示", hintInput),
    field("科目", subject),
    field("学段", grade),
  );

  const uploadBtn = el("button", "btn btn-primary", "开始识别") as HTMLButtonElement;
  uploadBtn.type = "button";
  const uploadMsg = el("p", "muted kb-msg", "");
  const actions = el("div", "kb-import-actions");
  actions.append(uploadBtn, uploadMsg);

  uploadBtn.onclick = async () => {
    const f = fileInput.files?.[0];
    if (!f) {
      uploadMsg.textContent = "请先选择 PDF";
      return;
    }
    if (!hintInput.value.trim() || hintInput.value.trim().toLowerCase().endsWith(".pdf")) {
      uploadMsg.textContent = "请填写不含 .pdf 的书名提示";
      return;
    }
    uploadBtn.disabled = true;
    uploadMsg.textContent = "上传中…";
    try {
      const out = await kb.upload(f, {
        label: labelInput.value.trim(),
        textbook_hint: hintInput.value.trim(),
        subject: subject.value,
        grade_band: grade.value,
      });
      uploadMsg.textContent = `已入队：${out.label}。可到「书库」查看进度。`;
      setFile(null);
      opts.onQueued(out);
    } catch (e) {
      uploadMsg.textContent = `失败：${(e as Error).message}`;
    } finally {
      uploadBtn.disabled = false;
    }
  };

  const adv = el("details", "kb-import-adv");
  const sum = document.createElement("summary");
  sum.textContent = "MinerU 识别设置";
  adv.appendChild(sum);
  mountKbSettings(adv);

  work.append(drop, grid, actions, adv);
  host.appendChild(work);
}
