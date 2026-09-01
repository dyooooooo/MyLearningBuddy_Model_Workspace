import assert from "node:assert/strict";
import test from "node:test";
import {
  blockLabelFromText,
  buildFocusChip,
  buildFocusComposerText,
  focusConversationTitle,
  nextStampId,
  resolveFocusFromPath,
} from "./pageFocus.ts";

test("nextStampId increments per tag", () => {
  const counts: Record<string, number> = {};
  assert.equal(nextStampId("P", counts), "p-1");
  assert.equal(nextStampId("p", counts), "p-2");
  assert.equal(nextStampId("H2", counts), "h2-1");
});

test("blockLabelFromText trims and clips", () => {
  assert.equal(blockLabelFromText("  例 3  "), "例 3");
  assert.equal(blockLabelFromText(""), "");
  const long = "甲".repeat(50);
  const out = blockLabelFromText(long, 40);
  assert.equal(out.length, 40);
  assert.ok(out.endsWith("…"));
});

test("resolveFocusFromPath needs book and page", () => {
  assert.equal(resolveFocusFromPath([], { bookId: "" }), null);
  assert.equal(resolveFocusFromPath([{ dataset: { pageNo: "3" } }], { bookId: "" }), null);
  assert.equal(resolveFocusFromPath([], { bookId: "b1" }), null);
});

test("resolveFocusFromPath reads page then nearest block", () => {
  const path = [
    {
      getAttribute: (name: string) => (name === "data-kb-block" ? "p-3" : name === "data-kb-block-label" ? "例 3" : null),
    },
    {
      classList: { contains: (c: string) => c === "kb-page-block" },
      dataset: { pageNo: "12" },
      getAttribute: (name: string) => (name === "data-page-no" ? "12" : null),
    },
  ];
  assert.deepEqual(resolveFocusFromPath(path, { bookId: "book-1", bookLabel: "沪教高中数学" }, "选中的一句"), {
    book_id: "book-1",
    book: "沪教高中数学",
    page_no: 12,
    block_id: "p-3",
    block_label: "例 3",
    quote: "选中的一句",
  });
});

test("composer text and chip and title", () => {
  const d = { book_id: "b1", book: "沪教", page_no: 12, block_id: "p-3", block_label: "例 3" };
  const text = buildFocusComposerText(d);
  assert.match(text, /【聚焦目标】/);
  assert.match(text, /book_id=b1/);
  assert.match(text, /page_no=12/);
  assert.match(text, /block_id=p-3/);
  assert.equal(buildFocusChip(d), "聚焦：沪教 · 第 12 页 · 例 3");
  assert.equal(focusConversationTitle(d), "沪教 · 第 12 页");
  assert.equal(focusConversationTitle({ book_id: "b1", page_no: 2 }), "b1 · 第 2 页");
});
