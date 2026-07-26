/**
 * MathJax 3 (SVG) — browser LaTeX → HTML (no external font assets).
 */
import { mathjax } from "mathjax-full/js/mathjax.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import { SVG } from "mathjax-full/js/output/svg.js";
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";
// Side-effect imports: listing a package name in ``packages`` is NOT enough —
// each package's configuration module must be imported so it registers its
// macro table. Without these the AMS symbols (\leqslant, \varnothing, ...)
// and cases environment fail with <merror>. Must stay in lockstep with the
// server bridge (learning-buddy-api/app/math/mathjax_render.mjs).
import "mathjax-full/js/input/tex/base/BaseConfiguration.js";
import "mathjax-full/js/input/tex/ams/AmsConfiguration.js";
import "mathjax-full/js/input/tex/newcommand/NewcommandConfiguration.js";
import "mathjax-full/js/input/tex/configmacros/ConfigMacrosConfiguration.js";
import "mathjax-full/js/input/tex/boldsymbol/BoldsymbolConfiguration.js";
import "mathjax-full/js/input/tex/cases/CasesConfiguration.js";

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);

const tex = new TeX({
  packages: ["base", "ams", "newcommand", "configmacros", "boldsymbol", "cases"],
});
const svg = new SVG({ fontCache: "none" });
const doc = mathjax.document("", { InputJax: tex, OutputJax: svg });

function hasRenderError(html: string): boolean {
  return (
    !html ||
    html.includes("data-mjx-error") ||
    html.includes('title="ParseError') ||
    html.includes("<merror")
  );
}

export function renderLatexToHtml(
  latex: string,
  display: boolean,
): { html: string; error: boolean } {
  const trimmed = latex.trim();
  if (!trimmed) {
    return { html: "", error: true };
  }
  try {
    const node = doc.convert(trimmed, { display });
    const html = adaptor.innerHTML(node);
    return { html, error: hasRenderError(html) };
  } catch {
    return { html: "", error: true };
  }
}
