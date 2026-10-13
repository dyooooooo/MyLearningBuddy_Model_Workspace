import assert from "node:assert/strict";
import test from "node:test";
import { paneStillOwned } from "./paneOwn.ts";

test("paneStillOwned writes only when host is still this page", () => {
  assert.equal(paneStillOwned({ isConnected: true }, 7, 7), true);
  assert.equal(paneStillOwned({ isConnected: false }, 7, 7), false);
  assert.equal(paneStillOwned({ isConnected: true }, 7, 8), false);
});
