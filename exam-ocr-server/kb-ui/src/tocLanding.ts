export type LandingEntry = {
  pdf_page?: number | null;
  is_chapter?: boolean;
  level?: number;
};

/**
 * 打开一本书落在哪一页。
 *
 * 停在目录里第一条带文件页的条目。前面如果是「前言」，就打开前言那一页，
 * 不再跳到后面的第一章。整本都没有文件页时留在第 1 页（封面）。
 */
export function landingPage(entries: readonly LandingEntry[]): number {
  const first = entries.find((row) => Number(row.pdf_page) > 0);
  return first ? Number(first.pdf_page) : 1;
}
