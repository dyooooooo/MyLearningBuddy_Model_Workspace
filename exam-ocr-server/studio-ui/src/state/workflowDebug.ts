// Import-line workflow trace → right Inspector (MinerU / 35B ops).

export type WorkflowStepStatus = "running" | "ok" | "error" | "info";

export interface WorkflowStep {
  id: string;
  at: number;
  status: WorkflowStepStatus;
  title: string;
  detail?: string;
}

export interface WorkflowFocus {
  line: "mineru" | "b35";
  jobId: string;
  pageNo?: number;
  blockId?: string;
  label?: string;
}

export interface WorkflowSnapshot {
  focus: WorkflowFocus | null;
  steps: WorkflowStep[];
  lastResult: Record<string, unknown> | null;
}

const MAX_STEPS = 40;
const EVENT = "studio:workflow-debug";

let focus: WorkflowFocus | null = null;
let steps: WorkflowStep[] = [];
let lastResult: Record<string, unknown> | null = null;
let seq = 0;

function emit(): void {
  document.dispatchEvent(new CustomEvent(EVENT, { detail: getWorkflowSnapshot() }));
}

export function getWorkflowSnapshot(): WorkflowSnapshot {
  return {
    focus,
    steps: [...steps],
    lastResult: lastResult ? { ...lastResult } : null,
  };
}

export function onWorkflowDebug(fn: (s: WorkflowSnapshot) => void): () => void {
  const handler = ((e: CustomEvent<WorkflowSnapshot>) => fn(e.detail)) as EventListener;
  document.addEventListener(EVENT, handler);
  return () => document.removeEventListener(EVENT, handler);
}

export function setWorkflowFocus(next: WorkflowFocus | null): void {
  focus = next;
  emit();
}

export function clearWorkflowSteps(): void {
  steps = [];
  lastResult = null;
  emit();
}

export function pushWorkflowStep(
  status: WorkflowStepStatus,
  title: string,
  detail?: string,
): string {
  seq += 1;
  const id = `w${seq}`;
  steps = [
    ...steps.slice(-(MAX_STEPS - 1)),
    { id, at: Date.now(), status, title, detail: detail || "" },
  ];
  emit();
  return id;
}

export function updateWorkflowStep(
  id: string,
  status: WorkflowStepStatus,
  detail?: string,
): void {
  steps = steps.map((s) =>
    s.id === id
      ? { ...s, status, detail: detail !== undefined ? detail : s.detail }
      : s,
  );
  emit();
}

export function setWorkflowResult(result: Record<string, unknown> | null): void {
  lastResult = result;
  emit();
}

/** Convenience: wrap an async op with running → ok/error steps. */
export async function withWorkflowStep<T>(
  title: string,
  run: () => Promise<T>,
  summarize?: (value: T) => string,
): Promise<T> {
  const id = pushWorkflowStep("running", title);
  try {
    const value = await run();
    updateWorkflowStep(id, "ok", summarize ? summarize(value) : "完成");
    return value;
  } catch (e) {
    updateWorkflowStep(id, "error", e instanceof Error ? e.message : String(e));
    throw e;
  }
}
