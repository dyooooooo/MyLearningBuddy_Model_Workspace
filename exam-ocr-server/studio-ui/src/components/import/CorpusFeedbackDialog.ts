// MinerU/corpus feedback dialog — isolated from B35FeedbackDialog.

import { corpus } from "../../api/corpus";
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

export function openCorpusFeedbackDialog(opts: {
  jobId: string;
  pageNo: number;
  pageEl: HTMLElement;
  onSubmitted?: () => void;
}): void {
  const overlay = el("div", "b35-feedback-overlay");
  const dialog = el("div", "b35-feedback-dialog");
  const title = el("h3", "", `第 ${opts.pageNo} 页 · 反馈问题（MinerU）`);
  const preview = document.createElement("img");
  preview.className = "b35-feedback-preview";
  preview.alt = "页面截图预览";

  const textarea = document.createElement("textarea");
  textarea.className = "b35-feedback-text";
  textarea.placeholder = "描述问题现象与期望";
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
      msg.textContent = "截图失败，将仅提交文字";
    }
  }

  async function runReprocessAndReload(): Promise<void> {
    msg.textContent = "重渲染中…";
    await corpus.reprocessPage(opts.jobId, opts.pageNo, { mode: "layout_content" });
    const res = await corpus.getImportPageHtml(opts.jobId, opts.pageNo);
    const { sanitizeHtml } = await import("../../util/html-sanitize");
    opts.pageEl.innerHTML = sanitizeHtml(String(res.html || ""));
    typesetTextbookMath(opts.pageEl);
    await refreshShot();
  }

  reprocessFirstBtn.onclick = () => void runReprocessAndReload().catch((e) => {
    msg.textContent = `重渲染失败：${(e as Error).message}`;
  });
  recaptureBtn.onclick = () => void refreshShot();
  cancelBtn.onclick = () => overlay.remove();
  submitBtn.onclick = async () => {
    const text = textarea.value.trim();
    if (!text) {
      msg.textContent = "请填写问题描述";
      return;
    }
    submitBtn.disabled = true;
    msg.textContent = "提交中…";
    try {
      const out = await corpus.createFeedback(opts.jobId, opts.pageNo, {
        body: text,
        tags: [...selectedTags],
        screenshot: shotBlob,
      });
      const fid = out?.feedback?.id;
      if (fid) {
        try {
          await corpus.buildCursorPack(fid);
          msg.textContent = "已提交并生成 Cursor Pack";
        } catch {
          msg.textContent = "已提交（Pack 生成失败，可稍后重试）";
        }
      } else {
        msg.textContent = "已提交";
      }
      opts.onSubmitted?.();
      setTimeout(() => overlay.remove(), 600);
    } catch (e) {
      msg.textContent = `失败：${(e as Error).message}`;
      submitBtn.disabled = false;
    }
  };

  actions.append(reprocessFirstBtn, recaptureBtn, cancelBtn, submitBtn);
  dialog.append(title, preview, textarea, tagsRow, msg, actions);
  overlay.appendChild(dialog);
  overlay.onclick = (e) => {
    if (e.target === overlay) overlay.remove();
  };
  document.body.appendChild(overlay);
  void refreshShot();
}
