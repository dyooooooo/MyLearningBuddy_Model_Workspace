/** Numeric sort for import page chunks — avoids lexicographic "第 10 页" before "第 7 页". */

const PAGE_CHUNK_ID = /:p(\d+)$/;
const PAGE_LABEL = /第\s*(\d+)\s*页/;

export function pageSortKey(
  nodeId: string,
  label: string,
  sourcePage?: unknown,
): [number, number, string] {
  const page = Number(sourcePage);
  if (Number.isFinite(page) && page > 0) {
    return [0, page, ""];
  }
  const idMatch = PAGE_CHUNK_ID.exec(nodeId);
  if (idMatch) {
    return [0, Number(idMatch[1]), ""];
  }
  const labelMatch = PAGE_LABEL.exec(label || "");
  if (labelMatch) {
    return [0, Number(labelMatch[1]), ""];
  }
  return [1, 0, label || ""];
}

export function comparePageOrder(
  a: { id: string; label: string; meta?: Record<string, unknown> },
  b: { id: string; label: string; meta?: Record<string, unknown> },
): number {
  const ka = pageSortKey(a.id, a.label, a.meta?.source_page);
  const kb = pageSortKey(b.id, b.label, b.meta?.source_page);
  if (ka[0] !== kb[0]) return ka[0] - kb[0];
  if (ka[1] !== kb[1]) return ka[1] - kb[1];
  return ka[2].localeCompare(kb[2], "zh-CN");
}
