export type IrSpan = {
  t?: string;
  c?: string;
  latex?: string;
};

export type IrBlock = {
  id: string;
  type: string;
  kind: string;
  text?: string;
  asset?: string;
  caption?: string;
  width_frac?: number;
  col?: number;
  row?: number;
  level?: number;
  box_kind?: string;
  table_html?: string;
  latex?: string;
  lines?: IrSpan[][];
};

export type IrChrome = {
  headers?: string[];
  footers?: string[];
  page_numbers?: string[];
  footnotes?: { order?: number; text?: string }[];
};

export type IrPage = {
  page?: number;
  book?: string;
  blocks?: IrBlock[];
  chrome?: IrChrome;
};

export function irAssetUrl(bookId: string, path: string): string {
  const raw = (path || "").replace(/\\/g, "/").trim();
  if (!raw) return "";
  if (raw.startsWith("/api/") || raw.startsWith("http://") || raw.startsWith("https://")) {
    return raw;
  }
  const name = raw.split("/").pop() || "";
  if (!name) return "";
  const key = `kb/pdfs/${bookId}/assets/mineru/${name}`;
  return `/api/v1/kb/proxy/asset?key=${encodeURIComponent(key)}`;
}

export function stripBoxPunct(text: string): string {
  return String(text || "").trim().replace(/[：:﹕]+$/g, "");
}

/** 正文 HTML 由知识库 Agent workspace 的 page-ir/paint.mjs 生成。 */
export async function fillIrPage(host: HTMLElement, page: IrPage, bookId: string): Promise<void> {
  const { kb } = await import("./api");
  const { fillShadow } = await import("./surfaces");
  const { stampBlocks } = await import("./pageFocus");
  const { typesetTextbookMath } = await import("./util/math-render");
  let html = "";
  try {
    const painted = await kb.surfacePaint("page-ir", { page, book_id: bookId });
    html = painted.html || "";
  } catch {
    html = "";
  }
  await fillShadow(host, "page-ir", html || "<p>（本页无文本）</p>");
  const inner = host.shadowRoot?.querySelector(".kb-surface-inner");
  if (inner) {
    stampBlocks(inner);
    typesetTextbookMath(inner);
  }
}
