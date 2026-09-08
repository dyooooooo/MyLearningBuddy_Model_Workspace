import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const logs = [];

function capture(level, args) {
  const text = args.map((item) => {
    if (typeof item === "string") return item;
    try {
      return JSON.stringify(item);
    } catch {
      return String(item);
    }
  }).join(" ");
  logs.push({ level, text: text.slice(0, 2000) });
}

const origWarn = console.warn.bind(console);
const origError = console.error.bind(console);
console.warn = (...args) => {
  capture("warn", args);
  origWarn(...args);
};
console.error = (...args) => {
  capture("error", args);
  origError(...args);
};

function fail(message) {
  process.stdout.write(JSON.stringify({ ok: false, error: String(message), logs }));
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
  const fn =
    op === "prepare" ? mod.prepare : op === "paint" ? mod.paint : op === "tag" ? mod.tag : null;
  if (typeof fn !== "function") {
    fail(`missing export ${op || "prepare|paint|tag"}`);
  }
  const ctx = input.ctx && typeof input.ctx === "object" ? input.ctx : {};
  const result =
    op === "paint" ? fn(input.payload ?? {}, ctx) : fn(String(input.markdown || ""), ctx);
  process.stdout.write(JSON.stringify({ ok: true, result, logs }));
} catch (err) {
  fail(err && err.message ? err.message : err);
}
