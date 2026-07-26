import { mathjax } from "mathjax-full/js/mathjax.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import { SVG } from "mathjax-full/js/output/svg.js";
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";

async function loadSideImports() {
  await import("mathjax-full/js/input/tex/base/BaseConfiguration.js");
  await import("mathjax-full/js/input/tex/ams/AmsConfiguration.js");
  await import("mathjax-full/js/input/tex/newcommand/NewcommandConfiguration.js");
  await import("mathjax-full/js/input/tex/configmacros/ConfigMacrosConfiguration.js");
  await import("mathjax-full/js/input/tex/boldsymbol/BoldsymbolConfiguration.js");
  await import("mathjax-full/js/input/tex/cases/CasesConfiguration.js");
}

function test(label, withSide) {
  const adaptor = liteAdaptor();
  RegisterHTMLHandler(adaptor);
  const tex = new TeX({
    packages: ["base", "ams", "newcommand", "configmacros", "boldsymbol", "cases"],
  });
  const svg = new SVG({ fontCache: "none" });
  const doc = mathjax.document("", { InputJax: tex, OutputJax: svg });
  const tests = [
    "2x+1 \\leqslant 0",
    "a \\leqslant x \\leqslant b",
    "\\varnothing",
    "\\complement_U A",
    "A \\subseteq B",
    "\\begin{cases}2x=4, \\\\ x+y=8 \\end{cases}",
    "\\alpha",
    "\\beta",
    "\\Rightarrow",
  ];
  console.log(`\n=== ${label} ===`);
  for (const t of tests) {
    try {
      const node = doc.convert(t, { display: false });
      const html = adaptor.innerHTML(node);
      const err =
        !html ||
        html.includes("data-mjx-error") ||
        html.includes("<merror") ||
        html.includes('title="ParseError');
      console.log(err ? "FAIL" : "OK ", t);
    } catch (e) {
      console.log("EXC", t, e.message);
    }
  }
}

// Browser-like: packages listed but NO side-effect imports
test("browser-like (no side imports)", false);

await loadSideImports();
test("server-like (with side imports)", true);
