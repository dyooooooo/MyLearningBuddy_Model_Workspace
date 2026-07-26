/**
 * Segment-based MathJax 3 renderer — no Unicode→LaTeX guessing on the client.
 * Backend (learning-buddy-api math_normalize) produces validated segments.
 */
import { renderLatexToHtml } from "./mathjax-core";

export type MathSegmentKind = "text" | "math";

export interface MathSegment {
  kind: MathSegmentKind;
  raw: string;
  latex?: string | null;
  valid?: boolean;
}

export interface RichText {
  raw?: string;
  segments?: MathSegment[];
}

function escapeTexText(s: string): string {
  return s.replace(/\\/g, "\\textbackslash ").replace(/[{}%&$#_^~]/g, (c) => `\\${c}`);
}

export function escapeHtml(str: string): string {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function segmentToTex(seg: MathSegment): string {
  if (seg.kind === "text") {
    return `\\text{${escapeTexText(seg.raw)}}`;
  }
  if (seg.valid && seg.latex) {
    return seg.latex;
  }
  return `\\text{${escapeTexText(seg.raw)}}`;
}

function richRaw(rich: RichText | null | undefined): string {
  if (!rich) return "";
  if (rich.raw) return rich.raw;
  return (rich.segments || []).map((s) => s.raw).join("");
}

/** Build one LaTeX string from backend segments. */
export function segmentsToLatex(segments: MathSegment[]): string | null {
  if (!segments?.length) return null;
  const body = segments.map(segmentToTex).join(" ").trim();
  return body || null;
}

/** Render pre-normalized segments as a single MathJax block. */
export function formatSegmentsHtml(
  segments: MathSegment[],
  opts: { display?: boolean } = {},
): string {
  if (!segments?.length) return "";

  const raw = segments.map((s) => s.raw).join("");
  const latex = segmentsToLatex(segments);
  if (!latex) {
    return `<span class="math-content math-plain">${escapeHtml(raw)}</span>`;
  }

  const { html, error } = renderLatexToHtml(latex, Boolean(opts.display));
  if (error || !html) {
    return `<span class="math-content math-plain">${escapeHtml(raw)}</span>`;
  }
  const cls = opts.display ? "math-content math-display" : "math-content";
  return `<span class="${cls}" data-latex="${escapeHtml(latex)}">${html}</span>`;
}

/** Render RichText, segments, or plain escaped text (no client-side math conversion). */
export function formatMathHtml(
  raw: string,
  opts: { display?: boolean; segments?: MathSegment[]; rich?: RichText | null } = {},
): string {
  if (raw == null && !opts.rich && !opts.segments) return "";

  const rich = opts.rich;
  const segments = opts.segments || rich?.segments;
  if (segments?.length) {
    return formatSegmentsHtml(segments, opts);
  }

  const text = String(raw ?? richRaw(rich) ?? "");
  if (!text) return "";
  return `<span class="math-content math-plain">${escapeHtml(text)}</span>`;
}

export function typesetMathIn(root: ParentNode | null): void {
  if (!root) return;
  root.querySelectorAll(".math-content[data-math-raw]").forEach((el) => {
    const raw = el.getAttribute("data-math-raw") || "";
    el.outerHTML = formatMathHtml(raw);
  });
}

declare global {
  interface Window {
    BuddyMath?: {
      formatSegmentsHtml: typeof formatSegmentsHtml;
      formatMathHtml: typeof formatMathHtml;
      typesetMathIn: typeof typesetMathIn;
      segmentsToLatex: typeof segmentsToLatex;
    };
  }
}

if (typeof window !== "undefined") {
  window.BuddyMath = {
    formatSegmentsHtml,
    formatMathHtml,
    typesetMathIn,
    segmentsToLatex,
  };
}
