import { agent, kb, type KbBook } from "./api";
import { shouldAbandonIdentify } from "./importLeave";
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
    el("p", "muted", "拖入 PDF 后，MinerU 先认前三页，Agent 抽科目学段并自动全书识别。认书、识别、清洗、建目录都在同一条对话里，底栏能看见进度。"),
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
  let chatStarted = false;
  let clearingFile = false;
  let identifyWait: Promise<void> = Promise.resolve();

  function setFormLocked(locked: boolean): void {
    labelInput.disabled = locked;
    hintInput.disabled = locked;
    subject.disabled = locked;
    grade.disabled = locked;
    category.disabled = locked;
  }

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

  function detachIdentify(): void {
    identifyGen += 1;
    uploadMsg.textContent = "";
    setFormLocked(false);
    draftId = "";
    identifyJobId = "";
    conversationId = "";
    committed = false;
    chatStarted = false;
  }

  async function abandonIdentify(): Promise<void> {
    const bookId = draftId;
    const jobId = identifyJobId;
    detachIdentify();
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
    chatStarted = false;
    setFormLocked(false);
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
      chatStarted = true;
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
      identifyJobId = chat.job_id || "";
      conversationId = String(chat.conversation_id || "").trim();
      committed = Boolean(identifyJobId);
      if (conversationId) {
        try {
          await kb.patchDraft(book.id, { conversation_id: conversationId });
        } catch {
          /* convert watch still reads the chat payload */
        }
      }
      if (gen !== identifyGen) {
        // Form was detached (user left import). Keep the queued chat.
        return;
      }
      if (conversationId) {
        window.dispatchEvent(
          new CustomEvent("kb:open-conversation", {
            detail: { id: conversationId, title: "判断教材科目学段" },
          }),
        );
      }
      uploadMsg.textContent = "Agent 正在根据前三页判断，随后自动全书识别。";
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

  const uploadMsg = el("p", "muted kb-msg", "");
  const actions = el("div", "kb-import-actions");
  actions.append(uploadMsg);

  async function markQueued(
    bookId: string,
    meta?: {
      label: string;
      textbook_hint: string;
      subject: string;
      grade_band: string;
      category?: string;
    },
  ): Promise<void> {
    if (committed) return;
    const sid = String(bookId || draftId || "").trim();
    if (!sid) return;
    committed = true;
    setFormLocked(true);
    const keepCid = conversationId;
    try {
      const row = await kb.get(sid);
      if (keepCid) {
        window.dispatchEvent(
          new CustomEvent("kb:open-conversation", {
            detail: { id: keepCid, title: row.label || meta?.label || "导入教材" },
          }),
        );
      }
      uploadMsg.textContent = `已入队：${row.label || meta?.label || sid}。全书识别在底栏同一条对话里。`;
      opts.onQueued(row);
    } catch {
      uploadMsg.textContent = `已写入判断${meta?.label ? `：${meta.label}` : ""}。全书识别已开始。`;
    }
  }

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
    uploadMsg.textContent = "已写入判断，Agent 正在全书识别。";
    void markQueued(sid || draftId, meta);
  }

  onLive((ev) => {
    if (ev.type === "import_meta") {
      const meta = parseImportMeta(ev);
      if (meta) fillFromMeta(meta, String(ev.book_id || ""));
      return;
    }
    if (isJob(ev) && ev.kind === "convert") {
      const payload =
        ev.payload && typeof ev.payload === "object"
          ? (ev.payload as Record<string, unknown>)
          : {};
      const bid = String(payload.book_id || "");
      if (bid && bid === draftId) void markQueued(bid);
      return;
    }
    if (!identifyJobId || !isJob(ev) || ev.job_id !== identifyJobId) return;
    const meta = importMetaFromJob(ev);
    if (meta) fillFromMeta(meta);
  });

  host.addEventListener("kb:leave-import", () => {
    resetDrop();
    if (!shouldAbandonIdentify({ committed, identifyJobId, chatStarted })) {
      detachIdentify();
      return;
    }
    void abandonIdentify();
  });
}
