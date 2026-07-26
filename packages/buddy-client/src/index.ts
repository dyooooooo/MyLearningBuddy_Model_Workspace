/**
 * Shared Buddy / Studio client surface.
 * Studio currently lives under exam-ocr-server/studio-ui; this package re-exports
 * the stable contracts for future React/Next consumers (M5+).
 *
 * Prefer generating OpenAPI types via tools/gen-api-types.sh into consumers.
 */

export type { ApiError } from "../../../exam-ocr-server/studio-ui/src/api/http";
export { formatApiError, studioFetch } from "../../../exam-ocr-server/studio-ui/src/api/http";
export type { ImportJob, ImportJobMeta, CorpusNode } from "../../../exam-ocr-server/studio-ui/src/api/types";
export { IMPORT_JOB_TERMINAL } from "../../../exam-ocr-server/studio-ui/src/services/importJobObserver";

/** Build SSE URL for import job events (access_token for EventSource). */
export function importJobEventsUrl(jobId: string, accessToken: string): string {
  const tok = encodeURIComponent(accessToken);
  return `/api/v1/studio/proxy/import/jobs/${encodeURIComponent(jobId)}/events?access_token=${tok}`;
}
