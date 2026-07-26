/** Typed Studio event registry — names + payload shapes. */

export type StudioEventMap = {
  "studio:reveal-node": { nodeId: string };
  "studio:import-workspace-focus": undefined;
  "studio:import-hub": undefined;
  "studio:import-new": undefined;
  "studio:import-progress": undefined;
  "studio:expand-right-dock": undefined;
  "studio:toggle-ai-terminal": undefined;
  "studio:open-ai-terminal": undefined;
  "studio:inspector-tab": { tab?: string };
  "studio:page-preview": { pageNo?: number; jobId?: string };
};

export type StudioEventName = keyof StudioEventMap;

export const STUDIO_EVENTS = Object.keys({
  "studio:reveal-node": 1,
  "studio:import-workspace-focus": 1,
  "studio:import-hub": 1,
  "studio:import-new": 1,
  "studio:import-progress": 1,
  "studio:expand-right-dock": 1,
  "studio:toggle-ai-terminal": 1,
  "studio:open-ai-terminal": 1,
  "studio:inspector-tab": 1,
  "studio:page-preview": 1,
} satisfies Record<StudioEventName, 1>) as StudioEventName[];
