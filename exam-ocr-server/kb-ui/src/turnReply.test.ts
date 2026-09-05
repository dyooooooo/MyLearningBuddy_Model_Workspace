import assert from "node:assert/strict";
import test from "node:test";
import { replyText } from "./turnReply.ts";

test("copy uses the finished assistant reply", () => {
  assert.equal(
    replyText({ result: { reply: "第 4 页已改。" }, payload: { message: "请改资料库" } }),
    "第 4 页已改。",
  );
});

test("copy prefers draft over the user prompt when the turn stopped", () => {
  assert.equal(
    replyText({ status: "cancelled", payload: { message: "请改资料库" }, result: {} }, "先看第 4 页"),
    "先看第 4 页",
  );
});

test("copy stays empty when there is no assistant text", () => {
  assert.equal(replyText({ status: "failed", payload: { message: "请改资料库" }, result: {} }), "");
  assert.equal(replyText({ payload: { message: "请改资料库" } }), "");
});
