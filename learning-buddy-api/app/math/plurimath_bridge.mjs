#!/usr/bin/env node
/**
 * Plurimath + MathJax validation bridge.
 *
 * One-shot (legacy):
 *   stdin:  {"text":"...","format":"unicode"|"asciimath"|"latex"}
 *   stdout: {"latex":"...","valid":bool,"error":"..."}
 *
 * Persistent (--serve): NDJSON request/response, one MathJax load for many calls.
 *   stdin line:  {"text":"...","format":"..."}  OR  {"items":[{...},...]}
 *   stdout line: {...}  OR  {"results":[...]}
 */
import { createInterface } from "node:readline";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { validateLatex } from "./mathjax_render.mjs";

const require = createRequire(import.meta.url);
const Plurimath = require("@plurimath/plurimath").default;

function respond(obj) {
  process.stdout.write(`${JSON.stringify(obj)}\n`);
}

function handleOne(input) {
  const text = String(input.text || "");
  const format =
    input.format === "asciimath"
      ? "asciimath"
      : input.format === "latex"
        ? "latex"
        : "unicode";
  if (!text) {
    return { latex: null, valid: false, error: "empty" };
  }
  // ``latex`` mode: input is already LaTeX — MathJax validate only.
  if (format === "latex") {
    const check = validateLatex(text);
    if (!check.valid) {
      return { latex: null, valid: false, error: "mathjax_render_failed" };
    }
    return { latex: text, valid: true, error: "" };
  }
  const formula = new Plurimath(text, format);
  const latex = formula.toLatex();
  if (!latex || !String(latex).trim()) {
    return { latex: null, valid: false, error: "empty_latex" };
  }
  const check = validateLatex(String(latex));
  if (!check.valid) {
    return { latex: null, valid: false, error: "mathjax_render_failed" };
  }
  return { latex: String(latex), valid: true, error: "" };
}

function handleRequest(input) {
  if (Array.isArray(input.items)) {
    return { results: input.items.map((it) => {
      try {
        return handleOne(it || {});
      } catch (err) {
        return {
          latex: null,
          valid: false,
          error: err instanceof Error ? err.message : String(err),
        };
      }
    }) };
  }
  return handleOne(input || {});
}

const serve = process.argv.includes("--serve");

if (serve) {
  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on("line", (line) => {
    const trimmed = String(line || "").trim();
    if (!trimmed) {
      respond({ latex: null, valid: false, error: "empty" });
      return;
    }
    try {
      respond(handleRequest(JSON.parse(trimmed)));
    } catch (err) {
      respond({
        latex: null,
        valid: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  });
  rl.on("close", () => process.exit(0));
} else {
  try {
    const line = readFileSync(0, "utf8").trim();
    const input = JSON.parse(line || "{}");
    respond(handleRequest(input));
  } catch (err) {
    respond({
      latex: null,
      valid: false,
      error: err instanceof Error ? err.message : String(err),
    });
  }
}
