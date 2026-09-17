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

/** Import-chain job that should take the dock. */
export function isImportChatJob(job: {
  kind?: string;
  payload?: Record<string, unknown> | null;
}): boolean {
  const payload = job.payload && typeof job.payload === "object" ? job.payload : {};
  const taskId = String(payload.task_id || "");
  if (job.kind === "printed_toc") {
    return taskId === "import-book-toc" || Boolean(jobConversationId(job));
  }
  if (job.kind === "convert") {
    return Boolean(jobConversationId(job));
  }
  if (job.kind !== "chat") return false;
  const trigger = String(payload.trigger || "");
  if (trigger === "kb.import.completed") return true;
  if (trigger === "kb.import.dropped") return true;
  if (trigger === "task.import-page-refine.completed") return true;
  if (trigger === "task.import-book-toc.completed") return true;
  if (taskId === "import-page-refine" || taskId === "import-book-toc" || taskId === "write-toc-pages") return true;
  return String(payload.message || "").includes("教材已导入完成");
}
