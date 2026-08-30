import assert from "node:assert/strict";
import test from "node:test";
import { bookSortLabel, parseBookSort, sortBooks } from "./bookSort.ts";

const books = [
  { label: "必修第一册", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-03-01T00:00:00Z" },
  { label: "选择性必修第三册", created_at: "2026-02-01T00:00:00Z", updated_at: "2026-02-01T00:00:00Z" },
  { label: "必修第二册", created_at: "2026-03-01T00:00:00Z", updated_at: "2026-01-15T00:00:00Z" },
];

test("parseBookSort falls back to recent update", () => {
  assert.equal(parseBookSort("nope"), "updated-desc");
  assert.equal(parseBookSort("title-asc"), "title-asc");
});

test("bookSortLabel keeps title compact", () => {
  assert.equal(bookSortLabel("updated-desc"), "最近更新");
  assert.equal(bookSortLabel("title-asc"), "标题 A → Z");
});

test("updated-desc puts later edits first", () => {
  assert.deepEqual(
    sortBooks(books, "updated-desc").map((b) => b.label),
    ["必修第一册", "选择性必修第三册", "必修第二册"],
  );
});

test("created-asc puts earlier imports first", () => {
  assert.deepEqual(
    sortBooks(books, "created-asc").map((b) => b.label),
    ["必修第一册", "选择性必修第三册", "必修第二册"],
  );
});

test("title-asc uses Chinese pinyin order", () => {
  assert.deepEqual(
    sortBooks(books, "title-asc").map((b) => b.label),
    ["必修第二册", "必修第一册", "选择性必修第三册"],
  );
});
