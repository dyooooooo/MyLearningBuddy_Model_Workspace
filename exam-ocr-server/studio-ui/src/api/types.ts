// Corpus (MinerU line) shared types.
// OpenAPI dumps live in ./generated/ (excluded from tsc; regenerate via tools/gen-api-types.sh).
// No `[key: string]: any` — use explicit optional fields; unknown bags only on meta.

export interface CorpusNode {
  id: string;
  label: string;
  node_type: string;
  status: string;
  summary: string;
  kind?: string;
  subject?: string;
  grade_band?: string;
  body_md?: string;
  full_text?: string;
  parent_id?: string;
  links?: Record<string, unknown>;
  meta: Record<string, unknown>;
  vector_indexed?: boolean;
}

export interface TreeNode {
  id: string;
  label: string;
  node_type: string;
  status?: string;
  kind?: string;
  has_children?: boolean;
  children?: TreeNode[];
  meta?: Record<string, unknown>;
  child_count?: number;
  meta_hint?: string;
}

export interface LinkEntry {
  id: string;
  label: string;
  node_id: string;
  rel_type: string;
  from_id?: string;
  to_id?: string;
  node_type?: string;
  status?: string;
}

export interface NodeLinks {
  hidden_count: number;
  structural: LinkEntry[];
  kp: LinkEntry[];
  tags: LinkEntry[];
  other: LinkEntry[];
  inlinks: LinkEntry[];
}

export interface ContentPack {
  pack_id: string;
  label: string;
  subject?: string;
  grade_band?: string;
}

export interface CorpusStats {
  total_nodes?: number;
  by_type?: Record<string, number>;
  by_status?: Record<string, number>;
  published_unindexed?: number;
  milvus_schema_ok?: boolean;
  milvus_production_count?: number;
}

export interface ImportJobMeta {
  progress?: string;
  ocr_progress?: string;
  ocr_page?: number;
  ocr_total?: number;
  chunk_progress?: string;
  missing_pages?: number[];
  pages_ready?: number | number[];
  content_list_source?: string;
  page_ranges?: string;
  [key: string]: unknown;
}

export interface ImportJob {
  id: string;
  status?: string;
  label?: string;
  parse_engine?: string;
  page_count?: number;
  progress?: string;
  error?: string;
  meta?: ImportJobMeta;
  created_at?: string;
  updated_at?: string;
  textbook_node_id?: string;
}

export interface PipelineStatus {
  import_queue_depth?: number;
  worker_alive?: boolean;
  last_error?: string;
}

export interface WorkQueueNode {
  id: string;
  label: string;
  status: string;
  node_type?: string;
  meta?: Record<string, unknown>;
}

export interface InboxItem {
  id: string;
  title: string;
  status?: string;
  proposed_node_ids?: string[];
}

export interface WorkQueueData {
  draft_chunks: WorkQueueNode[];
  review_nodes: WorkQueueNode[];
  unindexed_published: WorkQueueNode[];
  failed_imports: ImportJob[];
  inbox_ready: InboxItem[];
  structure_review_imports?: ImportJob[];
}

export interface ReindexJobStatus {
  job_id: string;
  status?: string;
  done: number;
  total: number;
  error?: string;
  errors?: string[];
}

export interface GraphNode {
  id: string;
  label?: string;
  node_type: string;
  status?: string;
}

export interface GraphEdge {
  id: string;
  from_id: string;
  to_id: string;
  rel_type: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
