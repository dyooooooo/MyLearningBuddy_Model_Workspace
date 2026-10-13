/**
 * MathJax 3 CommonHTML — 把 LaTeX 排成网页里的字，不画成图。
 * 宏包与 learning-buddy-api/app/math/mathjax_render.mjs 同一套。
 * 字体从 /api/v1/kb/agent/surfaces/page-ir/fonts 读。
 */
import { mathjax } from "mathjax-full/js/mathjax.js";
import { TeX } from "mathjax-full/js/input/tex.js";
import { CHTML } from "mathjax-full/js/output/chtml.js";
import { liteAdaptor } from "mathjax-full/js/adaptors/liteAdaptor.js";
import { RegisterHTMLHandler } from "mathjax-full/js/handlers/html.js";
import { STATE } from "mathjax-full/js/core/MathItem.js";
import "mathjax-full/js/input/tex/base/BaseConfiguration.js";
import "mathjax-full/js/input/tex/ams/AmsConfiguration.js";
import "mathjax-full/js/input/tex/newcommand/NewcommandConfiguration.js";
import "mathjax-full/js/input/tex/configmacros/ConfigMacrosConfiguration.js";
import "mathjax-full/js/input/tex/boldsymbol/BoldsymbolConfiguration.js";
import "mathjax-full/js/input/tex/cases/CasesConfiguration.js";
import "mathjax-full/js/input/tex/mhchem/MhchemConfiguration.js";
import "mathjax-full/js/input/tex/extpfeil/ExtpfeilConfiguration.js";

export const CHTML_FONT_URL = "/api/v1/kb/agent/surfaces/page-ir/fonts";

const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);

const tex = new TeX({
  packages: ["base", "ams", "newcommand", "configmacros", "boldsymbol", "cases", "mhchem", "extpfeil"],
});
const chtml = new CHTML({
  fontURL: CHTML_FONT_URL,
  adaptiveCSS: false,
  matchFontHeight: true,
});
const doc = mathjax.document("", { InputJax: tex, OutputJax: chtml });
const MathItem = doc.options.MathItem;
const _cache = new Map();

function hasRenderError(html) {
  return (
    !html ||
    html.includes("data-mjx-error") ||
    html.includes('title="ParseError') ||
    html.includes("<merror") ||
    html.includes("mjx-merror")
  );
}

function emptyTypeset() {
  return { html: "", error: true, wEm: 0, hEm: 0 };
}

/** 排一次，同时量出宽高（em），给墨水框按框缩小字号。 */
export function typesetLatex(latex, display = false) {
  const trimmed = String(latex || "").trim();
  if (!trimmed) return emptyTypeset();
  const key = `${display ? "1" : "0"}\0${trimmed}`;
  const hit = _cache.get(key);
  if (hit) return hit;
  try {
    const mitem = new MathItem(trimmed, tex, Boolean(display));
    mitem.start.node = adaptor.body(doc.document);
    mitem.setMetrics(16, 8, 80 * 8, 1000000, 1);
    mitem.convert(doc, STATE.LAST);
    const root = mitem.typesetRoot || mitem.root;
    const bbox = chtml.getBBox(mitem, doc);
    const wEm = Number(bbox && bbox.w) || 0;
    const h = Number(bbox && bbox.h) || 0;
    const d = Number(bbox && bbox.d) || 0;
    // 不要给容器写 vertical-align。CHTML 的 mjx-math 是行内块，基线就是 TeX 基线，
    // 外面再按 -depth 拉一次等于整条公式又下沉 d em（实测第 18 页例 12 低 6.16px）。
    const html = adaptor.outerHTML(root);
    const hEm = h + d;
    const out = { html, error: hasRenderError(html), wEm, hEm };
    _cache.set(key, out);
    return out;
  } catch {
    const out = emptyTypeset();
    _cache.set(key, out);
    return out;
  }
}

/** @param {string} latex @param {boolean} [display] */
export function renderLatexToHtml(latex, display = false) {
  const { html, error } = typesetLatex(latex, display);
  return { html, error };
}

export function measureLatex(latex, display = false) {
  const { wEm, hEm, error } = typesetLatex(latex, display);
  return { wEm, hEm, error };
}

export function chtmlCssText() {
  return adaptor.innerHTML(chtml.styleSheet(doc));
}
