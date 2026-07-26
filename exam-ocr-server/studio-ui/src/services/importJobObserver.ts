/**
 * Single MinerU import-job observer (Data Plane).
 * Prefers SSE; falls back to Query poll when EventSource fails.
 */

import { corpus } from "../api/corpus";
import { ApiError } from "../api/http";
import type { ImportJob } from "../api/types";
import { getStudioToken } from "../auth/studioToken";
import { studioBus } from "../events/studioBus";
import { queryClient } from "../state/queryClient";
import { store } from "../state/store";
import { jobStatusLabel } from "../util/labels";

export const IMPORT_JOB_TERMINAL = new Set([
  "done",
  "succeeded",
  "failed",
  "error",
  "canceled",
  "cancelled",
  "chunked",
  "review",
  "published",
]);

const STATUS_PCT: Record<string, number> = {
  uploaded: 10,
  queued: 15,
  pending: 15,
  converting: 45,
  parsing: 45,
  running: 55,
  chunking: 70,
  chunked: 85,
  building: 78,
  embedding: 88,
  review: 92,
  published: 100,
  done: 100,
  succeeded: 100,
  failed: 100,
  error: 100,
  canceled: 100,
  cancelled: 100,
};

let activeJobId: string | null = null;
let stopHandles: Array<() => void> = [];
let consecutiveErrors = 0;

export function getActiveImportJobId(): string | null {
  return activeJobId;
}

function progressRaw(job: ImportJob): string {
  const meta = job.meta || {};
  const chunk = meta.chunk_progress != null ? String(meta.chunk_progress) : "";
  return String(
    job.progress ||
      meta.progress ||
      chunk ||
      meta.ocr_progress ||
      (meta.ocr_page != null && meta.ocr_total != null
        ? `${meta.ocr_page}/${meta.ocr_total}`
        : "") ||
      "",
  );
}

function progressPct(job: ImportJob): number {
  const raw = progressRaw(job);
  const m = /(\d+)\s*\/\s*(\d+)/.exec(raw);
  if (m) {
    const done = Number(m[1]);
    const total = Number(m[2]);
    if (total > 0) return Math.min(99, Math.max(5, Math.round((done / total) * 100)));
  }
  return STATUS_PCT[String(job.status || "")] ?? 30;
}

export function describeImportJob(job: ImportJob): { statusText: string; pct: number } {
  const label = job.label || "导入任务";
  const status = jobStatusLabel(job.status);
  const progress = progressRaw(job);
  const missing = Array.isArray(job.meta?.missing_pages) ? job.meta!.missing_pages! : [];
  const missingNote =
    missing.length > 0
      ? ` · 缺页 ${missing.slice(0, 8).join(",")}${missing.length > 8 ? "…" : ""}`
      : "";
  const source = job.meta?.content_list_source
    ? ` · JSON ${job.meta.content_list_source}`
    : "";
  const ranges = job.meta?.page_ranges ? ` · 页 ${job.meta.page_ranges}` : "";
  const suffix = progress ? `（${progress}）` : "";
  const statusText = job.error
    ? `${label}：${job.error}`
    : `${label} · ${status}${suffix}${source}${ranges}${missingNote}`;
  return { statusText, pct: progressPct(job) };
}

function applyJob(job: ImportJob): void {
  store.setImportJob(job);
  studioBus.emit("studio:import-progress");
  if (IMPORT_JOB_TERMINAL.has(String(job.status || ""))) {
    stopImportJobObserver();
    activeJobId = null;
  }
}

async function refreshJob(jobId: string): Promise<void> {
  const job = await queryClient.fetchQuery({
    queryKey: ["importJob", jobId],
    queryFn: () => corpus.importJob(jobId),
    staleTime: 0,
  });
  applyJob(job);
}

function startPollFallback(jobId: string): void {
  const interval = window.setInterval(() => {
    if (!activeJobId || activeJobId !== jobId) return;
    if (document.visibilityState === "hidden") return;
    void refreshJob(jobId).catch((e: unknown) => {
      consecutiveErrors += 1;
      const status = e instanceof ApiError ? e.status : 0;
      if (status === 404 || consecutiveErrors >= 8) {
        stopImportJobObserver();
        activeJobId = null;
      }
    });
  }, 2500);
  stopHandles.push(() => window.clearInterval(interval));
}

function startSse(jobId: string): boolean {
  try {
    const tok = encodeURIComponent(getStudioToken());
    const url = `/api/v1/studio/proxy/import/jobs/${encodeURIComponent(jobId)}/events?access_token=${tok}`;
    const es = new EventSource(url);
    const onAny = () => {
      consecutiveErrors = 0;
      void refreshJob(jobId).catch(() => undefined);
    };
    es.addEventListener("job.progress", onAny);
    es.addEventListener("page.ready", onAny);
    es.addEventListener("job.terminal", onAny);
    es.onmessage = onAny;
    es.onerror = () => {
      es.close();
      // Fall back to poll once; don't loop reconnect storms.
      if (activeJobId === jobId) startPollFallback(jobId);
    };
    stopHandles.push(() => es.close());
    return true;
  } catch {
    return false;
  }
}

/** Begin tracking a MinerU import job. */
export function startImportJobObserver(jobId: string): void {
  activeJobId = jobId;
  consecutiveErrors = 0;
  stopImportJobObserver();
  activeJobId = jobId;

  void refreshJob(jobId).catch(() => undefined);
  if (!startSse(jobId)) {
    startPollFallback(jobId);
  }
}

export function stopImportJobObserver(): void {
  for (const s of stopHandles) s();
  stopHandles = [];
}

export function clearImportJobObserver(): void {
  stopImportJobObserver();
  activeJobId = null;
  store.setImportJob(null);
  studioBus.emit("studio:import-progress");
}

export const startImportProgressPolling = startImportJobObserver;
export const stopImportProgressPolling = clearImportJobObserver;
