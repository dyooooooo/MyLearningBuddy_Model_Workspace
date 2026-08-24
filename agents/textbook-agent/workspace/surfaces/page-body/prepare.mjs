import { wrapBareMath } from "./lib/bare-math.mjs";
import { polishLeads, polishLists, polishPageProse, liftHeadings } from "./lib/page-prose.mjs";
import { liftPageTables } from "./lib/page-table.mjs";
import { liftPageToc } from "./lib/page-toc.mjs";

export function prepare(md, ctx = {}) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const printToPdf = ctx.printToPdf && typeof ctx.printToPdf === "object" ? ctx.printToPdf : {};
  const bookId = String(ctx.bookId || "");
  const cleaned = polishPageProse(md || "");
  const structured = liftPageToc(liftPageTables(cleaned, bookId, profile), printToPdf, profile);
  return wrapBareMath(polishLeads(polishLists(liftHeadings(structured), profile), profile), profile);
}
