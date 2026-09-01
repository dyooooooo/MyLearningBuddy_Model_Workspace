import { wrapBareMath } from "./lib/bare-math.mjs";
import { hasKbFences, paintFences } from "./lib/fences.mjs";
import { tag } from "./tag.mjs";

export function prepare(md, ctx = {}) {
  const raw = md || "";
  const mid = hasKbFences(raw) ? raw : tag(raw, ctx);
  return wrapBareMath(paintFences(mid, ctx), ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {});
}
