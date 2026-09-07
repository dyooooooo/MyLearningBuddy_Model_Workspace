import { wrapBareMath } from "./lib/bare-math.mjs";
import { hasKbFences, paintFences, stackVerseDates, stackWorkHeads, wrapChemMods } from "./lib/fences.mjs";
import { sizeImages } from "./lib/fig-size.mjs";
import { liftPageTables } from "./lib/page-table.mjs";
import { tag } from "./tag.mjs";

export function prepare(md, ctx = {}) {
  const raw = md || "";
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const bookId = String(ctx.bookId || ctx.book_id || "");
  const mid = hasKbFences(raw) ? liftPageTables(raw, bookId, profile) : tag(raw, ctx);
  const painted = stackVerseDates(
    stackWorkHeads(wrapChemMods(wrapBareMath(paintFences(mid, ctx), profile))),
  );
  // Last step on purpose: earlier passes match on `![alt](url)` (section icons,
  // table lifting), so sizing must not turn images into HTML ahead of them.
  return sizeImages(painted, ctx);
}
