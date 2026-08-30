import { agent, kb, type KbBook } from "./api";
import { IDENTIFY_TEMPLATE, importMetaFromJob, parseImportMeta, renderIdentifyMessage } from "./importMeta";
import { isJob, onLive } from "./live";
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
    el("p", "muted", "拖入 PDF 后，MinerU 先认前三页，再交给 Agent 抽科目、学段、显示名和书名提示；点开始识别才全书入库清洗。"),
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

  let draftId = "";
  let identifyGen = 0;
  let identifyJobId = "";
  let conversationId = "";
  let committed = false;
  let clearingFile = false;
  let identifyWait: Promise<void> = Promise.resolve();

  function applyMeta(meta: {
    label: string;
    textbook_hint: string;
    subject: string;
    grade_band: string;
    category?: string;
  }): void {
    labelInput.value = meta.label;
    hintInput.value = meta.textbook_hint;
    subject.value = meta.subject;
    grade.value = meta.grade_band;
    if (meta.category) category.value = meta.category;
  }

  async function dropIfDraft(bookId: string): Promise<void> {
    if (!bookId) return;
    try {
      const row = await kb.get(bookId);
      if (row.status === "draft") await kb.removeDraft(bookId);
    } catch {
      /* missing or already converting */
    }
  }

  async function abandonIdentify(): Promise<void> {
    identifyGen += 1;
    uploadMsg.textContent = "";
    const bookId = draftId;
    const jobId = identifyJobId;
    draftId = "";
    identifyJobId = "";
    conversationId = "";
    committed = false;
    if (jobId) {
      try {
        await agent.cancelJob(jobId);
      } catch {
        /* already done */
      }
    }
    await dropIfDraft(bookId);
  }

  async function startIdentify(file: File): Promise<void> {
    const gen = ++identifyGen;
    draftId = "";
    identifyJobId = "";
    conversationId = "";
    committed = false;
    uploadMsg.textContent = "正在用 MinerU 识别前三页…";
    try {
      const book = await kb.stageDraft(file);
      if (gen !== identifyGen) {
        await dropIfDraft(book.id);
        return;
      }
      draftId = book.id;
      const out = await kb.identify(book.id);
      if (gen !== identifyGen) {
        await dropIfDraft(book.id);
        return;
      }
      uploadMsg.textContent = "前三页已出，交给 Agent 抽科目学段…";
      const chat = await agent.chat({
        message: renderIdentifyMessage(
          IDENTIFY_TEMPLATE,
          book.id,
          file.name,
          String(out.preview || ""),
        ),
        task_id: "import-page-refine",
        trigger: "kb.import.dropped",
      });
      if (gen !== identifyGen) {
        if (chat.job_id) {
          try {
            await agent.cancelJob(chat.job_id);
          } catch {
            /* abandoned */
          }
        }
        await dropIfDraft(book.id);
        return;
      }
      identifyJobId = chat.job_id || "";
      conversationId = String(chat.conversation_id || "").trim();
      if (conversationId) {
        try {
          await kb.patchDraft(book.id, { conversation_id: conversationId });
        } catch {
          /* form can still fill from import_meta */
        }
        window.dispatchEvent(
          new CustomEvent("kb:open-conversation", {
            detail: { id: conversationId, title: "判断教材科目学段" },
          }),
        );
      }
      uploadMsg.textContent = "Agent 正在根据前三页填写，可改完再点开始识别。";
    } catch (e) {
      if (gen === identifyGen) {
        const detail = (e as Error).message || "";
        uploadMsg.textContent =
          /not found|404/i.test(detail)
            ? "草稿已取消，请重新拖入 PDF。"
            : `前三页判断未完成：${detail}`;
      }
    }
  }

  function resetDrop(): void {
    clearingFile = true;
    fileInput.value = "";
    dropName.textContent = "尚未选择文件";
    drop.classList.remove("has-file");
    clearingFile = false;
  }

  function setFile(file: File | null): void {
    if (!file) {
      resetDrop();
      identifyWait = abandonIdentify();
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
    const stem = file.name.replace(/\.pdf$/i, "");
    if (!labelInput.value.trim()) labelInput.value = stem;
    if (!hintInput.value.trim()) hintInput.value = stem;
    identifyWait = (async () => {
      await abandonIdentify();
      await startIdentify(file);
    })();
  }

  drop.onclick = () => fileInput.click();
  fileInput.onclick = (ev) => ev.stopPropagation();
  fileInput.onchange = () => {
    if (clearingFile) return;
    setFile(fileInput.files?.[0] || null);
  };
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
    ["physics", "物理"],
    ["chemistry", "化学"],
    ["biology", "生物"],
    ["english", "英语"],
  ]);
  const grade = document.createElement("select");
  selectOpts(grade, [
    ["hs", "高中"],
    ["ms", "初中"],
    ["ps", "小学"],
  ]);
  const category = document.createElement("select");
  selectOpts(category, [
    ["textbook", "教材"],
    ["knowledge", "知识点"],
    ["syllabus", "考纲"],
    ["material", "资料"],
    ["homework", "作业"],
  ]);

  const grid = el("div", "kb-import-grid");
  grid.append(
    field("显示名", labelInput),
    field("书名提示", hintInput),
    field("科目", subject),
    field("学段", grade),
    field("类型", category),
  );

  const uploadBtn = el("button", "btn btn-primary", "开始识别") as HTMLButtonElement;
  uploadBtn.type = "button";
  const uploadMsg = el("p", "muted kb-msg", "");
  const actions = el("div", "kb-import-actions");
  actions.append(uploadBtn, uploadMsg);

  uploadBtn.onclick = async () => {
    const f = fileInput.files?.[0];
    if (!f && !draftId) {
      uploadMsg.textContent = "请先选择 PDF";
      return;
    }
    const hint =
      hintInput.value.trim() ||
      labelInput.value.trim() ||
      (f ? f.name.replace(/\.pdf$/i, "") : "");
    if (!hint || hint.toLowerCase().endsWith(".pdf")) {
      uploadMsg.textContent = "请填写不含 .pdf 的书名提示";
      return;
    }
    hintInput.value = hint;
    uploadBtn.disabled = true;
    uploadMsg.textContent = "等前三页识别并交给 Agent…";
    try {
      await identifyWait;
    } catch {
      /* start with whatever we have */
    }
    const ready = fileInput.files?.[0] || null;
    if (!ready && !draftId) {
      uploadMsg.textContent = "请先选择 PDF";
      uploadBtn.disabled = false;
      return;
    }
    uploadMsg.textContent = "开始识别全书…";
    try {
      const out = await kb.upload(draftId ? null : ready, {
        label: labelInput.value.trim() || hint,
        textbook_hint: hint,
        subject: subject.value,
        grade_band: grade.value,
        category: category.value,
        book_id: draftId || undefined,
        conversation_id: conversationId || undefined,
      });
      committed = true;
      identifyGen += 1;
      identifyJobId = "";
      conversationId = "";
      draftId = "";
      resetDrop();
      uploadMsg.textContent = `已入队：${out.label}。全书识别完成后，同一条导入对话会抽目录和清洗。`;
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

  function fillFromMeta(
    meta: { label: string; textbook_hint: string; subject: string; grade_band: string; category?: string },
    bookId = "",
  ): void {
    const sid = String(bookId || "").trim();
    if (!draftId || (sid && sid !== draftId)) return;
    applyMeta(meta);
    uploadMsg.textContent = "已填入判断结果，可改完再点开始识别。";
  }

  onLive((ev) => {
    if (ev.type === "import_meta") {
      const meta = parseImportMeta(ev);
      if (meta) fillFromMeta(meta, String(ev.book_id || ""));
      return;
    }
    if (!identifyJobId || !isJob(ev) || ev.job_id !== identifyJobId) return;
    const meta = importMetaFromJob(ev);
    if (meta) fillFromMeta(meta);
  });

  host.addEventListener("kb:leave-import", () => {
    if (committed) return;
    resetDrop();
    void abandonIdentify();
  });
}
