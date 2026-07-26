import { import35b } from "../../api/import35b";
import { typesetTextbookMath } from "../../util/math-render";
import { el } from "../../util/dom";

const TAG_OPTIONS = [
  { id: "blank_page", label: "空白页" },
  { id: "math", label: "公式" },
  { id: "layout", label: "排版" },
  { id: "other", label: "其他" },
];

async function capturePageScreenshot(pageEl: HTMLElement): Promise<Blob | null> {
  try {
    const { toJpeg } = await import("html-to-image");
    await typesetTextbookMath(pageEl);
    const dataUrl = await toJpeg(pageEl, {
      quality: 0.88,
      pixelRatio: 1,
      cacheBust: true,
    });
    const res = await fetch(dataUrl);
    return await res.blob();
  } catch {
    return null;
  }
}

export function openB35FeedbackDialog(opts: {
  jobId: string;
  pageNo: number;
  pageEl: HTMLElement;
  onSubmitted?: () => void;
}): void {
  const overlay = el("div", "b35-feedback-overlay");
  const dialog = el("div", "b35-feedback-dialog");
  const title = el("h3", "", `第 ${opts.pageNo} 页 · 反馈问题`);
  const preview = document.createElement("img");
  preview.className = "b35-feedback-preview";
  preview.alt = "页面截图预览";

  const textarea = document.createElement("textarea");
  textarea.className = "b35-feedback-text";
  textarea.placeholder = "描述问题现象与期望（例如：主栏空白、公式显示错误）";
  textarea.rows = 4;

  const tagsRow = el("div", "b35-feedback-tags");
  const selectedTags = new Set<string>();
  for (const opt of TAG_OPTIONS) {
    const chip = el("button", "b35-feedback-tag", opt.label) as HTMLButtonElement;
    chip.type = "button";
    chip.onclick = () => {
      if (selectedTags.has(opt.id)) {
        selectedTags.delete(opt.id);
        chip.classList.remove("b35-feedback-tag--on");
      } else {
        selectedTags.add(opt.id);
        chip.classList.add("b35-feedback-tag--on");
      }
    };
    tagsRow.appendChild(chip);
  }

  const reprocessRow = el("label", "b35-feedback-reprocess");
  const reprocessBefore = document.createElement("input");
  reprocessBefore.type = "checkbox";
  reprocessBefore.className = "b35-feedback-reprocess-check";
  reprocessRow.append(reprocessBefore, document.createTextNode(" 提交前先重识别本页"));

  const msg = el("div", "b35-feedback-msg muted", "");
  const actions = el("div", "b35-feedback-actions");
  const reprocessFirstBtn = el("button", "btn-link", "先重识别再留言") as HTMLButtonElement;
  const recaptureBtn = el("button", "btn-link", "重新截图") as HTMLButtonElement;
  const cancelBtn = el("button", "btn-link", "取消") as HTMLButtonElement;
  const submitBtn = el("button", "btn-primary", "提交反馈") as HTMLButtonElement;
  reprocessFirstBtn.type = "button";
  recaptureBtn.type = "button";
  cancelBtn.type = "button";
  submitBtn.type = "button";

  let shotBlob: Blob | null = null;

  async function refreshShot(): Promise<void> {
    msg.textContent = "截图中…";
    shotBlob = await capturePageScreenshot(opts.pageEl);
    if (shotBlob) {
      preview.src = URL.createObjectURL(shotBlob);
      preview.style.display = "block";
      msg.textContent = "";
    } else {
      preview.style.display = "none";
      msg.textContent = "截图失败，将仅提交文字（服务端会引用输入页图）";
    }
  }

  async function runReprocessAndReload(): Promise<void> {
    msg.textContent = "重识别中…";
    await import35b.reprocessPage(opts.jobId, opts.pageNo, { mode: "full" });
    const { sanitizeHtml } = await import("../../util/html-sanitize");
    opts.pageEl.innerHTML = sanitizeHtml(await import35b.pageHtml(opts.jobId, opts.pageNo));
    typesetTextbookMath(opts.pageEl);
    await refreshShot();
    msg.textContent = "已重识别，可继续留言";
  }

  const close = () => overlay.remove();

  cancelBtn.onclick = close;
  overlay.onclick = (e) => {
    if (e.target === overlay) close();
  };
  recaptureBtn.onclick = () => void refreshShot();
  reprocessFirstBtn.onclick = async () => {
    reprocessFirstBtn.disabled = true;
    submitBtn.disabled = true;
    try {
      await runReprocessAndReload();
    } catch (e) {
      msg.textContent = `重识别失败：${(e as Error).message}`;
    } finally {
      reprocessFirstBtn.disabled = false;
      submitBtn.disabled = false;
    }
  };

  submitBtn.onclick = async () => {
    const body = textarea.value.trim();
    if (!body) {
      msg.textContent = "请填写问题描述";
      return;
    }
    submitBtn.disabled = true;
    reprocessFirstBtn.disabled = true;
    try {
      if (reprocessBefore.checked) {
        msg.textContent = "提交前重识别…";
        await runReprocessAndReload();
      }
      msg.textContent = "提交中…";
      const fd = new FormData();
      fd.append("body", body);
      fd.append("tags", JSON.stringify([...selectedTags]));
      if (shotBlob) {
        fd.append("screenshot", shotBlob, "screenshot.jpg");
      }
      const out = await import35b.createFeedback(opts.jobId, opts.pageNo, fd);
      const fid = out?.feedback?.id;
      if (fid && confirm("已提交。是否生成 Cursor 修复包？")) {
        try {
          const pack = await import35b.buildCursorPack(fid);
          msg.textContent = `Pack: ${pack.pack_path || pack.absolute_path || "已生成"}`;
        } catch (pe) {
          msg.textContent = `反馈已提交；Pack 失败：${(pe as Error).message}`;
        }
      } else {
        msg.textContent = "已提交";
      }
      opts.onSubmitted?.();
      window.setTimeout(close, fid ? 1200 : 400);
    } catch (e) {
      msg.textContent = `失败：${(e as Error).message}`;
      submitBtn.disabled = false;
      reprocessFirstBtn.disabled = false;
    }
  };

  actions.append(reprocessFirstBtn, recaptureBtn, cancelBtn, submitBtn);
  dialog.append(title, preview, textarea, tagsRow, reprocessRow, msg, actions);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  void refreshShot();
}
