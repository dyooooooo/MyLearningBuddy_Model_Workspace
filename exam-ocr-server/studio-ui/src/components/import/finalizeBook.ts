// 定稿（finalize）：把当前这一版定为该教材的唯一版本，其余同书导入连数据一起删除。
//
// 破坏性操作，所以永远「先预览再执行」：预览由 buddy-api 给出要删的版本清单和
// 告警（最典型的是要删的那版改进点比保留的这版更多），确认后才带 ack_warnings 执行。

import { copilot } from "../../api/copilot";
import { corpus, type FinalizeBookReport } from "../../api/corpus";
import { MINERU_JOB_DELETED } from "./MinerUImportTree";

function shortId(id: string): string {
  return id.slice(0, 8);
}

const MATCH_TEXT: Record<string, string> = {
  fingerprint: "同一本书（书指纹一致）",
  same_file: "同一份 PDF 文件",
  same_body_pages: "正文页完全相同",
  similar_body_pages: "正文页高度相似（不是同一份文件）",
};

function describe(report: FinalizeBookReport): string {
  const keep = report.keep;
  const lines: string[] = [
    `定稿：${keep.label || shortId(keep.job_id)}（任务 ${shortId(keep.job_id)}）`,
    `保留 · 改进点 ${keep.improvement_points} 条 · 页补丁 ${keep.corrections} 条 · 节点 ${keep.node_count} 个`,
    "",
  ];

  if (report.remove.length === 0) {
    lines.push("没有检测到同一本书的其他导入版本，只会给这一版加上定稿锁。");
  } else {
    lines.push(`将永久删除同书的 ${report.remove.length} 个导入版本：`);
    for (const r of report.remove) {
      lines.push(
        `  · ${shortId(r.job_id)} ${r.label || "无标签"}（${r.status}）` +
          ` — ${MATCH_TEXT[r.match] || r.match}` +
          ` · 改进点 ${r.improvement_points} · 页补丁 ${r.corrections} · 节点 ${r.node_count}`,
      );
    }
    lines.push("");
    lines.push("删除范围：教材子树与向量、页反馈、改进点与页补丁、书指纹、对象存储文件、AI 对话。");
  }

  if (report.editions_to_drop.length > 0) {
    lines.push("");
    lines.push(
      "同时删除这些只被上述版本使用的专书方案（fork）：" +
        report.editions_to_drop.map((e) => e.edition_id).join("、"),
    );
  }

  if (report.warnings.length > 0) {
    lines.push("");
    lines.push("⚠ 告警：");
    for (const w of report.warnings) lines.push(`  · ${w}`);
  }

  lines.push("");
  lines.push("此操作不可撤销（撤销定稿只能解锁，删掉的版本不会回来）。确认执行？");
  return lines.join("\n");
}

/** 预览 → 用户确认 → 执行定稿。返回是否已执行。 */
export async function runFinalizeBook(jobId: string): Promise<boolean> {
  if (!jobId) return false;
  let report: FinalizeBookReport;
  try {
    report = await corpus.finalizeBookPreview(jobId);
  } catch (e) {
    alert(`定稿预览失败：${(e as Error).message}`);
    return false;
  }

  if (report.keep.already_finalized) {
    alert(`这一版已经定稿过（任务 ${shortId(jobId)}），无需重复操作。`);
    return false;
  }
  if (!confirm(describe(report))) return false;

  const removed = report.remove.map((r) => r.job_id);
  try {
    await corpus.finalizeBook(jobId, { dry_run: false, ack_warnings: true });
  } catch (e) {
    alert(`定稿失败：${(e as Error).message}`);
    return false;
  }

  // 任务行已在后端消失：清掉绑在它们上面的 AI 对话与本地面板状态。
  const { onImportJobDeleted } = await import("../CopilotPanel");
  for (const id of removed) {
    try {
      await copilot.cascadeByImportJob(id);
    } catch {
      // Copilot 不在线不影响定稿结果
    }
    onImportJobDeleted(id);
    document.dispatchEvent(
      new CustomEvent(MINERU_JOB_DELETED, { detail: { jobId: id } }),
    );
  }

  alert(
    `已定稿：${report.keep.label || shortId(jobId)}\n` +
      `删除同书版本 ${removed.length} 个。这本书现在是系统里唯一版本，删除已被锁定。`,
  );
  return true;
}
