import assert from "node:assert/strict";
import test from "node:test";
import { irAssetUrl, stripBoxPunct } from "./renderIr.ts";

test("irAssetUrl matches textbook-agent asset_url shape", () => {
  const url = irAssetUrl("0d100cbe267f45df91551d55b851c41c", "images/flow.jpg");
  assert.equal(
    url,
    "/api/v1/kb/proxy/asset?key=" +
      encodeURIComponent("kb/pdfs/0d100cbe267f45df91551d55b851c41c/assets/mineru/flow.jpg"),
  );
});

test("stripBoxPunct drops trailing colon so 栏目名不印两遍", () => {
  assert.equal(stripBoxPunct("思考与讨论："), "思考与讨论");
  assert.equal(stripBoxPunct("思考与讨论:"), "思考与讨论");
});
