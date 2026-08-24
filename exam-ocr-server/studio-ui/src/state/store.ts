import { createStore } from "zustand/vanilla";
import { subscribeWithSelector } from "zustand/middleware";
import { corpus } from "../api/corpus";
import type { CorpusNode, CorpusStats, ImportJob } from "../api/types";
import { comparePageOrder } from "../util/pageSort";
import { studioBus } from "../events/studioBus";

export type RibbonSection =
  | "textbook"
  | "import"
  | "search"
  | "todo"
  | "edition-ops"
  | "improvements";
export type ImportCenterView = "hub" | "preview" | "setup" | "mineru-settings";

const RIBBON_STORAGE_KEY = "studio-active-ribbon";

function loadRibbon(): RibbonSection {
  try {
    const v = localStorage.getItem(RIBBON_STORAGE_KEY);
    if (
      v === "textbook" ||
      v === "import" ||
      v === "search" ||
      v === "todo" ||
      v === "edition-ops" ||
      v === "improvements"
    ) {
      return v;
    }
  } catch {
    /* ignore */
  }
  return "textbook";
}

const _initialRibbon = loadRibbon();

export type StudioState = {
  currentNode: CorpusNode | null;
  stats: CorpusStats | null;
  activeImportJob: ImportJob | null;
  showGraph: boolean;
  selectedNodeId: string | null;
  activeRibbon: RibbonSection;
  subject: string;
  gradeBand: string;
  breadcrumb: { id: string; label: string }[];
  importMode: boolean;
  importCenterView: ImportCenterView;
};

const initialState: StudioState = {
  currentNode: null,
  stats: null,
  activeImportJob: null,
  showGraph: false,
  selectedNodeId: null,
  activeRibbon: _initialRibbon,
  subject: "math",
  gradeBand: "hs",
  breadcrumb: [],
  importMode: _initialRibbon === "import",
  importCenterView: "hub",
};

export const studioStore = createStore<StudioState>()(
  subscribeWithSelector(() => ({ ...initialState })),
);

function setState(partial: Partial<StudioState>): void {
  studioStore.setState(partial);
}

function jobUnchanged(prev: ImportJob | null, job: ImportJob | null): boolean {
  if (prev === job) return true;
  if (!prev || !job) return false;
  return (
    prev.id === job.id &&
    prev.status === job.status &&
    String(prev.updated_at || "") === String(job.updated_at || "") &&
    String(prev.progress || "") === String(job.progress || "") &&
    JSON.stringify(prev.meta || {}) === JSON.stringify(job.meta || {})
  );
}

/**
 * Compatibility facade: property getters + methods used across Studio.
 * Prefer `studioStore.subscribe(selector, cb)` for fine-grained updates.
 */
export const store = {
  get currentNode() {
    return studioStore.getState().currentNode;
  },
  set currentNode(v: CorpusNode | null) {
    setState({ currentNode: v });
  },
  get stats() {
    return studioStore.getState().stats;
  },
  set stats(v: CorpusStats | null) {
    setState({ stats: v });
  },
  get activeImportJob() {
    return studioStore.getState().activeImportJob;
  },
  get showGraph() {
    return studioStore.getState().showGraph;
  },
  set showGraph(v: boolean) {
    setState({ showGraph: v });
  },
  get selectedNodeId() {
    return studioStore.getState().selectedNodeId;
  },
  set selectedNodeId(v: string | null) {
    setState({ selectedNodeId: v });
  },
  get activeRibbon() {
    return studioStore.getState().activeRibbon;
  },
  get subject() {
    return studioStore.getState().subject;
  },
  set subject(v: string) {
    setState({ subject: v });
  },
  get gradeBand() {
    return studioStore.getState().gradeBand;
  },
  set gradeBand(v: string) {
    setState({ gradeBand: v });
  },
  get breadcrumb() {
    return studioStore.getState().breadcrumb;
  },
  set breadcrumb(v: { id: string; label: string }[]) {
    setState({ breadcrumb: v });
  },
  get importMode() {
    return studioStore.getState().importMode;
  },
  set importMode(v: boolean) {
    setState({ importMode: v });
  },
  get importCenterView() {
    return studioStore.getState().importCenterView;
  },
  set importCenterView(v: ImportCenterView) {
    setState({ importCenterView: v });
  },

  /** Full-state subscribe (legacy). Prefer subscribeSelector. */
  subscribe(fn: () => void): () => void {
    return studioStore.subscribe(fn);
  },

  subscribeSelector<T>(
    selector: (s: StudioState) => T,
    cb: (selected: T, prev: T) => void,
  ): () => void {
    return studioStore.subscribe(selector, cb);
  },

  emit(): void {
    // Zustand notifies on setState; no-op kept for call sites.
  },

  async openInTextbook(nodeId: string): Promise<void> {
    setState({ importMode: false, activeRibbon: "textbook" });
    try {
      localStorage.setItem(RIBBON_STORAGE_KEY, "textbook");
    } catch {
      /* ignore */
    }
    await store.openNode(nodeId);
  },

  async openNode(id: string): Promise<void> {
    if (id.startsWith("job:") || id.startsWith("inbox") || id.startsWith("folder:")) {
      return;
    }
    try {
      const ctx = await corpus.navContext(id);
      setState({
        currentNode: ctx.node,
        selectedNodeId: id,
        breadcrumb: ctx.breadcrumb?.length
          ? ctx.breadcrumb
          : [{ id: ctx.node.id, label: ctx.node.label }],
      });
    } catch {
      const node = await corpus.getNode(id);
      setState({
        currentNode: node,
        selectedNodeId: id,
        breadcrumb: [{ id: node.id, label: node.label }],
      });
    }
    studioBus.emit("studio:reveal-node", { nodeId: id });
  },

  setImportJob(job: ImportJob | null): void {
    const prev = studioStore.getState().activeImportJob;
    if (jobUnchanged(prev, job)) return;
    setState({ activeImportJob: job });
  },

  markSelected(id: string | null): void {
    setState({ selectedNodeId: id });
  },

  focusImportWorkspace(): void {
    const s = studioStore.getState();
    const patch: Partial<StudioState> = {
      importMode: true,
      currentNode: null,
    };
    if (s.activeRibbon !== "import") {
      patch.activeRibbon = "import";
      try {
        localStorage.setItem(RIBBON_STORAGE_KEY, "import");
      } catch {
        /* ignore */
      }
    }
    setState(patch);
    studioBus.emit("studio:import-workspace-focus");
  },

  showImportHub(): void {
    setState({ importCenterView: "hub" });
    store.focusImportWorkspace();
    studioBus.emit("studio:import-hub");
  },

  enterImportMode(): void {
    setState({ importCenterView: "setup" });
    store.focusImportWorkspace();
    studioBus.emit("studio:import-new");
  },

  showMinerUSettings(): void {
    setState({ importCenterView: "mineru-settings" });
    store.focusImportWorkspace();
  },

  showImportPreview(): void {
    setState({ importCenterView: "preview" });
  },

  hideImportSetup(): void {
    store.showImportPreview();
  },

  isImportSetupVisible(): boolean {
    return studioStore.getState().importCenterView === "setup";
  },

  exitImportMode(): void {
    if (!studioStore.getState().importMode) return;
    setState({ importMode: false });
  },

  toggleGraph(show?: boolean): void {
    const cur = studioStore.getState().showGraph;
    setState({ showGraph: show ?? !cur });
  },

  setActiveRibbon(section: RibbonSection): void {
    const prev = studioStore.getState().activeRibbon;
    if (prev === section) return;
    try {
      localStorage.setItem(RIBBON_STORAGE_KEY, section);
    } catch {
      /* ignore */
    }
    if (section === "import") {
      setState({
        activeRibbon: section,
        importMode: true,
        currentNode: null,
        importCenterView: "hub",
      });
      studioBus.emit("studio:import-workspace-focus");
      studioBus.emit("studio:import-hub");
      return;
    }
    setState({ activeRibbon: section, importMode: false });
  },

  isImportWorkspace(): boolean {
    const s = studioStore.getState();
    return s.activeRibbon === "import" && s.importMode;
  },
};

export async function findFirstPageChunk(textbookId: string): Promise<string | null> {
  const sections = await corpus.tree("textbook", textbookId);
  const pageChunks: { id: string; meta?: Record<string, unknown> }[] = [];
  for (const sec of sections) {
    const chunks = await corpus.tree("textbook", sec.id);
    for (const c of chunks) {
      if (c.node_type !== "chunk" || c.status !== "draft") continue;
      const kind = String(c.meta?.chunk_kind || "");
      const page = Number(c.meta?.source_page || 0);
      if (kind === "page_import" || page > 0 || /:p\d{4}$/.test(c.id)) {
        pageChunks.push(c);
      }
    }
  }
  if (!pageChunks.length) return findFirstDraftChunk(textbookId);
  pageChunks.sort((a, b) =>
    comparePageOrder(
      { id: a.id, label: "", meta: a.meta },
      { id: b.id, label: "", meta: b.meta },
    ),
  );
  return pageChunks[0]?.id ?? null;
}

export async function findFirstDraftChunk(textbookId: string): Promise<string | null> {
  const sections = await corpus.tree("textbook", textbookId);
  for (const sec of sections) {
    const chunks = await corpus.tree("textbook", sec.id);
    const draft = chunks.find((c) => c.status === "draft" && c.node_type === "chunk");
    if (draft) return draft.id;
    if (sec.status === "draft" && sec.node_type === "chunk") return sec.id;
  }
  return null;
}
