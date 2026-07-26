// MinerU re-recognize dialog: PipeDebug context + user note → 35B.

import { corpus } from "../../api/corpus";
import { el } from "../../util/dom";
import { mountCorpusPageDebugDrawer } from "./CorpusPageDebugDrawer";
import {
  clearWorkflowSteps,
  pushWorkflowStep,
  setWorkflowFocus,
  setWorkflowResult,
  withWorkflowStep,
} from "../../state/workflowDebug";

export function formatApiError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err || "unknown");
  try {
    const j = JSON.parse(raw);
    if (typeof j?.detail === "string") return j.detail;
    if (j?.detail != null) return JSON.stringify(j.detail);
  } catch {
    /* plain text */
  }
  const m = raw.match(/Invalid [^"]+escape[^"]*/);
  if (m) return m[0];
  return raw.length > 400 ? `${raw.slice(0, 400)}…` : raw;
}

function summarizeOut(out: any): string {
  const parts = [
    `mode=${out?.mode ?? "?"}`,
    `patches=${out?.refine_patches ?? "?"}`,
    `changes=${out?.effective_changes ?? "?"}`,
  ];
  if (out?.noop) parts.push("noop");
  const js = out?.judge_summary;
  if (js && typeof js === "object") {
    parts.push(
      `judge keep_m=${js.keep_mineru ?? 0}/35b=${js.keep_35b ?? 0}/drop=${js.drop ?? 0}`,
    );
  }
  if (out?.fingerprint_hash) {
    parts.push(`fp ${String(out.fingerprint_hash).slice(0, 8)}`);
  }
  return parts.join(" · ");
}

export function openCorpusRerecognizeDialog(opts: {
  jobId: string;
  pageNo: number;
  blockId?: string;
  onDone?: (html?: string) => void;
}): void {
  const scope = opts.blockId ? "block" : "page";
  setWorkflowFocus({
    line: "mineru",
    jobId: opts.jobId,
    pageNo: opts.pageNo,
    blockId: opts.blockId,
    label:
      scope === "block"
        ? `第 ${opts.pageNo} 页 · 建议重识别块`
        : `第 ${opts.pageNo} 页 · 建议重识别`,
  });
  clearWorkflowSteps();
  pushWorkflowStep("info", "打开建议重识别对话框", opts.blockId || `page ${opts.pageNo}`);

  const overlay = el("div", "b35-feedback-overlay corpus-rerecog-overlay");
  const dialog = el("div", "b35-feedback-dialog corpus-rerecog-dialog");
  const title = el(
    "h3",
    "",
    scope === "block"
      ? `第 ${opts.pageNo} 页 · 块重新识别`
      : `第 ${opts.pageNo} 页 · 建议重识别`,
  );
  if (opts.blockId) {
    const sub = el("div", "muted corpus-rerecog-sub", `block: ${opts.blockId}`);
    dialog.append(title, sub);
  } else {
    dialog.append(title);
  }

  const stageLabel = el(
    "div",
    "corpus-rerecog-label",
    scope === "block"
      ? "调试输出（MinerU PipeDebug · 已定位到该块）"
      : "调试输出（MinerU PipeDebug）",
  );
  const debugHost = el("div", "corpus-rerecog-debug-host");

  const noteLabel = el("div", "corpus-rerecog-label", "改进建议（提交给 35B）");
  const textarea = document.createElement("textarea");
  textarea.className = "b35-feedback-text";
  textarea.rows = 4;
  textarea.placeholder =
    scope === "block"
      ? "例如：这段公式识别错了，应改为 …；侧栏应标 aside_right"
      : "例如：目录行错乱；页眉重复；第 3 段漏字；侧栏人物注被塞进正文…";

  const hint =
    scope === "page"
      ? el(
          "div",
          "muted corpus-rerecog-hint",
          "「改内容」保留块结构；「重建版面」可做合并/改栏（更慢）。不满意请点页上「重新识别」回滚到 MinerU 基线。",
        )
      : el("div", "muted corpus-rerecog-hint", "");

  const msg = el("div", "b35-feedback-msg muted", "");
  const actions = el("div", "b35-feedback-actions");
  const cancelBtn = el("button", "btn-link", "取消") as HTMLButtonElement;
  cancelBtn.type = "button";

  const layoutContentBtn = el(
    "button",
    "btn-primary",
    scope === "block" ? "提交并重新识别" : "改内容（保结构）",
  ) as HTMLButtonElement;
  layoutContentBtn.type = "button";

  const rebuildBtn =
    scope === "page"
      ? (el("button", "btn-primary corpus-rerecog-rebuild", "重建版面并优选") as HTMLButtonElement)
      : null;
  if (rebuildBtn) {
    rebuildBtn.type = "button";
    rebuildBtn.title = "35B 重建蓝图 → 与 MinerU 对齐裁决（耗时更长）";
  }

  actions.append(cancelBtn, layoutContentBtn);
  if (rebuildBtn) actions.append(rebuildBtn);

  dialog.append(stageLabel, debugHost, noteLabel, textarea, hint, msg, actions);
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  mountCorpusPageDebugDrawer(debugHost, opts.jobId, opts.pageNo, {
    autoOpen: true,
    embedded: true,
    focusBlockId: opts.blockId || "",
    summaryLabel: "MinerU PipeDebug（与右侧属性栏同步）",
  });

  cancelBtn.onclick = () => overlay.remove();
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) overlay.remove();
  });

  const run = async (mode: "layout_content" | "layout_rebuild" | "block") => {
    const note = textarea.value.trim();
    layoutContentBtn.disabled = true;
    if (rebuildBtn) rebuildBtn.disabled = true;
    cancelBtn.disabled = true;
    msg.textContent =
      mode === "layout_rebuild" ? "重建并优选中，请稍候…" : "35B 识别中，请稍候…";
    pushWorkflowStep("info", "提交建议", `${mode} · ${note.slice(0, 100) || "(无建议)"}`);
    try {
      let out: any;
      if (mode === "block" || opts.blockId) {
        out = await withWorkflowStep("调用 35B refine_block", () =>
          corpus.refineBlock(opts.jobId, opts.pageNo, opts.blockId!, {
            user_note: note,
          }),
        );
      } else {
        out = await withWorkflowStep(`调用 35B ${mode}`, () =>
          corpus.reprocessPage(opts.jobId, opts.pageNo, {
            mode,
            user_note: note,
          }),
        );
      }
      setWorkflowResult({
        mode: out?.mode,
        block_id: opts.blockId,
        refine_patches: out?.refine_patches,
        refine_patch_preview: out?.refine_patch_preview,
        effective_changes: out?.effective_changes,
        noop: out?.noop,
        judge_summary: out?.judge_summary,
        finding_count: out?.finding_count,
        persisted: out?.persisted,
        correction_id: out?.correction_id,
      });
      const html =
        typeof out?.html === "string"
          ? out.html
          : (
              await withWorkflowStep("拉取更新后 HTML", () =>
                corpus.getImportPageHtml(opts.jobId, opts.pageNo),
              )
            ).html;
      msg.textContent = `完成 · ${summarizeOut(out)}`;
      pushWorkflowStep("ok", "对话框重识别完成", msg.textContent);
      opts.onDone?.(String(html || ""));
      window.setTimeout(() => overlay.remove(), 500);
    } catch (e) {
      const err = formatApiError(e);
      msg.textContent = `失败：${err}`;
      pushWorkflowStep("error", "对话框重识别失败", err);
      layoutContentBtn.disabled = false;
      if (rebuildBtn) rebuildBtn.disabled = false;
      cancelBtn.disabled = false;
    }
  };

  layoutContentBtn.onclick = () => {
    void run(opts.blockId ? "block" : "layout_content");
  };
  if (rebuildBtn) {
    rebuildBtn.onclick = () => {
      void run("layout_rebuild");
    };
  }
}
