import assert from "node:assert/strict";
import test from "node:test";
import { shouldAbandonIdentify } from "./importLeave.ts";

test("leave-import abandons only when no identify chat exists yet", () => {
  assert.equal(shouldAbandonIdentify({ committed: false, identifyJobId: "" }), true);
  assert.equal(shouldAbandonIdentify({ committed: false, identifyJobId: "   " }), true);
});

test("leave-import keeps a waiting or committed identify job", () => {
  assert.equal(shouldAbandonIdentify({ committed: false, identifyJobId: "job-1" }), false);
  assert.equal(shouldAbandonIdentify({ committed: true, identifyJobId: "" }), false);
  assert.equal(shouldAbandonIdentify({ committed: true, identifyJobId: "job-1" }), false);
  assert.equal(shouldAbandonIdentify({ committed: false, identifyJobId: "", chatStarted: true }), false);
});
