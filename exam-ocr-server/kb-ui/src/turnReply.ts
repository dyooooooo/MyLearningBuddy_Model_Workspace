/** Assistant text a turn's 复制 button should put on the clipboard. */

export type ReplyJob = {
  status?: string;
  payload?: Record<string, unknown>;
  // 任务还没跑完时服务端给的是 null，不是缺字段。
  result?: Record<string, unknown> | null;
};

export function replyText(job: ReplyJob, draft = ""): string {
  const res = job.result || {};
  const done = String(res.reply || res.correct_solution || res.notes || "").trim();
  if (done) return done;
  // A stopped turn has no final reply; copying the user's own prompt is useless.
  return String(draft || "").trim();
}
