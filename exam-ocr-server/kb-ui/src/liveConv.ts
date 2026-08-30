export function pickLiveConversationId(
  rows: { id?: string; status?: string }[] | undefined,
): string {
  const hit = (rows || []).find((row) => row.status === "running" || row.status === "queued");
  return String(hit?.id || "").trim();
}

export function jobConversationId(job: {
  payload?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
}): string {
  const payload = job.payload && typeof job.payload === "object" ? job.payload : {};
  const result = job.result && typeof job.result === "object" ? job.result : {};
  const raw = String(payload.conversation_id || result.conversation_id || "").trim();
  if (!raw || raw === "none" || raw === "local") return "";
  return raw;
}

/** Chat started by「导入教材完成后」task — should take the dock. */
export function isImportChatJob(job: {
  kind?: string;
  payload?: Record<string, unknown> | null;
}): boolean {
  if (job.kind !== "chat") return false;
  const payload = job.payload && typeof job.payload === "object" ? job.payload : {};
  const trigger = String(payload.trigger || "");
  if (trigger === "kb.import.completed") return true;
  if (trigger === "kb.import.dropped") return true;
  if (trigger === "task.import-page-refine.completed") return true;
  if (String(payload.task_id || "") === "import-page-refine") return true;
  return String(payload.message || "").includes("教材已导入完成");
}
