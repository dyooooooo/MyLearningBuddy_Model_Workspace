/** 当前活跃的 MinerU 导入任务（左栏 / 中栏 / 右栏 AI 共用）。 */

export const ACTIVE_MINERU_JOB_KEY = "studio.mineru.activeImportJob";
/** 兼容旧键（Copilot 曾单独存） */
const LEGACY_COPILOT_JOB_KEY = "studio.copilot.activeImportJob";

export function persistActiveMinerUJob(jobId: string): void {
  const jid = String(jobId || "").trim();
  if (!jid) return;
  try {
    window.localStorage.setItem(ACTIVE_MINERU_JOB_KEY, jid);
    window.localStorage.setItem(LEGACY_COPILOT_JOB_KEY, jid);
  } catch {
    /* ignore */
  }
}

export function loadActiveMinerUJob(): string {
  try {
    return (
      window.localStorage.getItem(ACTIVE_MINERU_JOB_KEY) ||
      window.localStorage.getItem(LEGACY_COPILOT_JOB_KEY) ||
      ""
    ).trim();
  } catch {
    return "";
  }
}

export function clearActiveMinerUJob(jobId?: string): void {
  try {
    const cur = loadActiveMinerUJob();
    const jid = String(jobId || "").trim();
    if (!jid || cur === jid) {
      window.localStorage.removeItem(ACTIVE_MINERU_JOB_KEY);
      window.localStorage.removeItem(LEGACY_COPILOT_JOB_KEY);
    }
  } catch {
    /* ignore */
  }
}

/** 三栏对齐：打开中栏预览 + 左栏高亮；右栏由 MinerU preview 内 restoreAi 挂接。 */
export function focusMinerUJobTriPane(jobId: string, pageNo?: number): void {
  const id = String(jobId || "").trim();
  if (!id) return;
  persistActiveMinerUJob(id);
  document.dispatchEvent(new CustomEvent("studio:import-line-mineru"));
  document.dispatchEvent(
    new CustomEvent("studio:mineru-preview", {
      detail: { jobId: id, pageNo: pageNo && pageNo > 0 ? pageNo : undefined },
    }),
  );
}
