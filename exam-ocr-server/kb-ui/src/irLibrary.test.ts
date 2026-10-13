import assert from "node:assert/strict";
import test from "node:test";
import { epochAlive } from "./epoch.ts";

test("epochAlive drops stale openBook tokens", () => {
  assert.equal(epochAlive(3, 3), true);
  assert.equal(epochAlive(4, 3), false);
});
