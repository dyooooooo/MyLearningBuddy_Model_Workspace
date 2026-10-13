/** 宿主还在文档上、页号没变，才允许把这次读页结果写进去。 */
export function paneStillOwned(
  host: { isConnected?: boolean },
  pageNo: number,
  expectedPage: number,
): boolean {
  if (host.isConnected === false) return false;
  return expectedPage > 0 && pageNo === expectedPage;
}
