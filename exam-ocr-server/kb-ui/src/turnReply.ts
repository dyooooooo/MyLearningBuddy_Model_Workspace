/** Assistant text a turn's 复制 button should put on the clipboard. */

export type ReplyJob = {
  status?: string;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown>;
};

export function replyText(job: ReplyJob, draft = ""): string {
  const res = job.result || {};
  const done = String(res.reply || res.correct_solution || res.notes || "").trim();
  if (done) return done;
  // A stopped turn has no final reply; copying the user's own prompt is useless.
  return String(draft || "").trim();
}
