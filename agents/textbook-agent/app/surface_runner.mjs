import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

function fail(message) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(message) }));
  process.exit(1);
}

try {
  const input = JSON.parse(readFileSync(0, "utf8") || "{}");
  const cwd = resolve(String(input.cwd || process.cwd()));
  const entry = resolve(cwd, String(input.entry || ""));
  if (entry !== cwd && !entry.startsWith(`${cwd}/`)) {
    fail("entry outside slot");
  }
  const mod = await import(pathToFileURL(entry).href);
  const op = String(input.op || "");
  const fn = op === "prepare" ? mod.prepare : op === "paint" ? mod.paint : null;
  if (typeof fn !== "function") {
    fail(`missing export ${op || "prepare|paint"}`);
  }
  const ctx = input.ctx && typeof input.ctx === "object" ? input.ctx : {};
  const result = op === "prepare" ? fn(String(input.markdown || ""), ctx) : fn(input.payload ?? {}, ctx);
  process.stdout.write(JSON.stringify({ ok: true, result }));
} catch (err) {
  fail(err && err.message ? err.message : err);
}
