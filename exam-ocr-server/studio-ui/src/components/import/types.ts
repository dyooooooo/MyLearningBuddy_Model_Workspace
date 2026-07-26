export interface PanelHandle {
  setupRoot: HTMLElement;
  previewRoot: HTMLElement;
  refresh: () => void;
  /** 按导入任务恢复中栏预览（及右栏 AI）；可选 */
  restoreSessionForJob?: (jobId: string) => Promise<void>;
}
