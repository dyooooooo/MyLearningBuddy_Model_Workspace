import assert from "node:assert/strict";
import test from "node:test";
import { isImportChatJob, jobConversationId, pickLiveConversationId } from "./liveConv.ts";

test("pickLiveConversationId prefers running then queued", () => {
  assert.equal(pickLiveConversationId([{ id: "a", status: "completed" }, { id: "b", status: "queued" }]), "b");
});

test("jobConversationId skips local placeholders", () => {
  assert.equal(jobConversationId({ payload: { conversation_id: "local" } }), "");
  assert.equal(jobConversationId({ payload: { conversation_id: "c1" } }), "c1");
});

test("isImportChatJob matches builtin import task", () => {
  assert.equal(
    isImportChatJob({
      kind: "chat",
      payload: { task_id: "import-page-refine", conversation_id: "c1", message: "教材已导入完成。书号：b1。" },
    }),
    true,
  );
  assert.equal(isImportChatJob({ kind: "chat", payload: { trigger: "kb.import.completed" } }), true);
  assert.equal(isImportChatJob({ kind: "chat", payload: { trigger: "kb.import.dropped" } }), true);
  assert.equal(isImportChatJob({ kind: "chat", payload: { trigger: "task.import-page-refine.completed" } }), true);
  assert.equal(isImportChatJob({ kind: "chat", payload: { message: "随便问一句" } }), false);
  assert.equal(isImportChatJob({ kind: "grade", payload: { task_id: "import-page-refine" } }), false);
});
