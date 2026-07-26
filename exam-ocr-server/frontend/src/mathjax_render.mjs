/**
 * MathJax 3 (SVG) — shared LaTeX → HTML for Node validation bridge.
 */
import { mathjax } from "mathjax-full/js/mathjax.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import { SVG } from "mathjax-full/js/output/svg.js";
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";

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
