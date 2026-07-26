import { renderLatexToHtml } from "../src/mathjax_render.mjs";

function escapeTexText(s) {
  return s.replace(/\\/g, "\\textbackslash ").replace(/[{}%&$#_^~]/g, (c) => `\\${c}`);
}

function segmentToTex(seg) {
  if (seg.kind === "text") return `\\text{${escapeTexText(seg.raw)}}`;
  if (seg.valid && seg.latex) return seg.latex;
  return `\\text{${escapeTexText(seg.raw)}}`;
}

function segmentsToLatex(segments) {
  if (!segments?.length) return null;
  return segments.map(segmentToTex).join(" ").trim() || null;
}

function formatSegmentsHtml(segments) {
  const raw = segments.map((s) => s.raw).join("");
  const latex = segmentsToLatex(segments);
  if (!latex) return { html: raw, error: false };
  const { html, error } = renderLatexToHtml(latex, false);
  return { html, error, latex };
}

const fixtures = [
  {
    name: "subscripts",
    segments: [
      { kind: "math", raw: "x₁≠x₂", latex: "x_{1} \\neq x_{2}", valid: true },
    ],
  },
  {
    name: "mixed_cjk",
    segments: [
      { kind: "text", raw: "由题意得", valid: false },
      { kind: "math", raw: "2r+αr=10", latex: "2r + \\alpha r = 10", valid: true },
    ],
  },
  {
    name: "invalid_math_plain",
    segments: [{ kind: "math", raw: "{ x+3, x<-1 }", valid: false }],
  },
];

let failed = 0;
for (const fx of fixtures) {
  const { html, error } = formatSegmentsHtml(fx.segments);
  console.log(`--- ${fx.name}`);
  console.log("error:", error);
  if (error) {
    failed += 1;
    console.log(html.match(/data-mjx-error[^>]*/)?.[0] || html.slice(0, 120));
  }
}
process.exit(failed ? 1 : 0);
