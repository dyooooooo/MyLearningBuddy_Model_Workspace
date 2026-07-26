/**
 * MathJax 3 (SVG) — shared LaTeX → HTML for Node validation bridge.
 */
import { mathjax } from "mathjax-full/js/mathjax.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import { SVG } from "mathjax-full/js/output/svg.js";
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";
// Side-effect imports: in Node, listing a package name in ``packages`` is NOT
// enough — the package's configuration module must be imported so it registers
// its macros/symbols. Without this the AMS symbol table (``\leqslant``,
// ``\geqslant``, ``\varnothing``, ...) is missing and MathJax emits <merror>,
// so the bridge wrongly reports valid LaTeX as unrenderable. The browser bundle
// (tex-mml-chtml.js) already includes these, so this keeps server == client.
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

function hasRenderError(html) {
  return (
    !html ||
    html.includes("data-mjx-error") ||
    html.includes('title="ParseError') ||
    html.includes("<merror")
  );
}

/** @param {string} latex @param {boolean} [display] */
export function renderLatexToHtml(latex, display = false) {
  const trimmed = String(latex || "").trim();
  if (!trimmed) {
    return { html: "", error: true };
  }
  try {
    const node = doc.convert(trimmed, { display: Boolean(display) });
    const html = adaptor.innerHTML(node);
    return { html, error: hasRenderError(html) };
  } catch {
    return { html: "", error: true };
  }
}

/** @param {string} latex */
export function validateLatex(latex) {
  const { html, error } = renderLatexToHtml(latex, false);
  return { valid: !error, html };
}
