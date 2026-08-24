/** Typed Studio event registry — names + payload shapes. */

export type StudioEventMap = {
  "studio:reveal-node": { nodeId: string };
  "studio:import-workspace-focus": undefined;
  "studio:import-hub": undefined;
  "studio:import-new": undefined;
  "studio:import-progress": undefined;
  "studio:expand-right-dock": undefined;
  "studio:expand-bottom-dock": undefined;
  "studio:toggle-ai-terminal": undefined;
  "studio:open-ai-terminal": undefined;
  /** 属性栏右栏 ↔ 下栏切换（用户手动）。 */
  "studio:toggle-inspector-dock": undefined;
  /** 属性栏停靠位置变化后广播，供按钮文案同步。 */
  "studio:inspector-dock": { placement: "right" | "bottom" };
  "studio:inspector-tab": { tab?: string };
  "studio:page-preview": { pageNo?: number; jobId?: string };
  /** Double-click page/block in import preview → inject into AI dialog. */
  "studio:focus-import-target": {
    line: "mineru" | "35b";
    jobId: string;
    pageNo: number;
    blockId?: string;
  };
  /** MinerU 中栏实时流水线：解析进度 / 找症状 / 按手册改 */
  "studio:mineru-pipeline-tick": {
    job_id?: string;
    pipeline_phase?: string;
    status?: string;
    pages_ready?: number | string;
    autofix_round?: number;
    autofix_remaining?: number;
    autofix_done?: boolean;
    batch_types?: string[];
    batch_n?: number;
    known_n?: number;
    unknown_n?: number;
    park_reason?: string;
    symptom_rows?: Array<{
      suspect_type: string;
      known?: boolean;
      count?: number;
      pages?: number[];
      playbook?: string;
      remediation_kind?: string;
      landing_target?: string;
    }>;
  };
};

export type StudioEventName = keyof StudioEventMap;

export const STUDIO_EVENTS = Object.keys({
  "studio:reveal-node": 1,
  "studio:import-workspace-focus": 1,
  "studio:import-hub": 1,
  "studio:import-new": 1,
  "studio:import-progress": 1,
  "studio:expand-right-dock": 1,
  "studio:expand-bottom-dock": 1,
  "studio:toggle-ai-terminal": 1,
  "studio:open-ai-terminal": 1,
  "studio:toggle-inspector-dock": 1,
  "studio:inspector-dock": 1,
  "studio:inspector-tab": 1,
  "studio:page-preview": 1,
  "studio:focus-import-target": 1,
  "studio:mineru-pipeline-tick": 1,
} satisfies Record<StudioEventName, 1>) as StudioEventName[];
