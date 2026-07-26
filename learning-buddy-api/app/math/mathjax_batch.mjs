#!/usr/bin/env node
/**
 * v4 编译面 SSR 批量入口：一页一次 Node 调用。
 * stdin:  {"items":[{"latex":"...","display":false}, ...]}
 * stdout: {"results":[{"html":"<svg .../>","error":false}, ...]}
 */
import { readFileSync } from "node:fs";
import { renderLatexToHtml } from "./mathjax_render.mjs";

try {
  const input = JSON.parse(readFileSync(0, "utf8").trim() || "{}");
  const items = Array.isArray(input.items) ? input.items : [];
  const results = items.map((it) => {
    try {
      const { html, error } = renderLatexToHtml(String(it.latex || ""), Boolean(it.display));
      return { html: error ? "" : html, error };
    } catch {
      return { html: "", error: true };
    }
  });
  process.stdout.write(`${JSON.stringify({ results })}\n`);
} catch (err) {
  process.stdout.write(
    `${JSON.stringify({ results: [], fatal: err instanceof Error ? err.message : String(err) })}\n`,
  );
}
