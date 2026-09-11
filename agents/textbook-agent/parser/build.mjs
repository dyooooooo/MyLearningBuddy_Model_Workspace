import { build } from "esbuild";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outfile = resolve(here, "../workspace/surfaces/page-body/lib/md-parse.bundle.mjs");

await build({
  absWorkingDir: here,
  entryPoints: [resolve(here, "src/index.mjs")],
  outfile,
  bundle: true,
  format: "esm",
  platform: "node",
  target: "node18",
  legalComments: "none",
  logLevel: "info",
});

console.log(`wrote ${outfile}`);
