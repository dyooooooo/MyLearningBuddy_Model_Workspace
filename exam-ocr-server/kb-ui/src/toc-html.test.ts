import assert from "node:assert/strict";
import test from "node:test";
import { buildTocForest, renderTocHtml, tocEntryLevel } from "./util/toc-html.ts";

test("chapter owns the next section", () => {
  const forest = buildTocForest([
    { label: "第1章 集合与逻辑", is_chapter: true, level: 0, print_page: 1, pdf_page: 7 },
    { label: "1.1 集合初步", is_chapter: false, level: 1, print_page: 2 },
  ]);
  assert.equal(forest.length, 1);
  assert.equal(forest[0].children.length, 1);
  assert.equal(forest[0].children[0].entry.label, "1.1 集合初步");
});

test("same-level chapters stay siblings", () => {
  const forest = buildTocForest([
    { label: "引论", is_chapter: true, level: 0, print_page: 1 },
    { label: "第1部分", is_chapter: true, level: 0 },
    { label: "1 红绿灯", is_chapter: false, level: 1 },
    { label: "附录", is_chapter: true, level: 0 },
    { label: "附录1", is_chapter: false, level: 1 },
  ]);
  assert.deepEqual(forest.map((n) => n.entry.label), ["引论", "第1部分", "附录"]);
  assert.equal(forest[0].children.length, 0);
  assert.equal(forest[1].children[0].entry.label, "1 红绿灯");
  assert.equal(forest[2].children[0].entry.label, "附录1");
});

test("section owns subsections", () => {
  const forest = buildTocForest([
    { label: "1.2 常用逻辑用语", level: 1, print_page: 13 },
    { label: "1 命题", level: 2, print_page: 13 },
    { label: "2 充分条件", level: 2, print_page: 15 },
  ]);
  assert.equal(forest.length, 1);
  assert.deepEqual(forest[0].children.map((n) => n.entry.label), ["1 命题", "2 充分条件"]);
});

test("is_chapter wins over a non-zero level", () => {
  assert.equal(tocEntryLevel({ is_chapter: true, level: 2 }), 0);
});

test("fallback html has a caret only on branches", () => {
  const html = renderTocHtml(
    [
      { label: "第1章", is_chapter: true, print_page: 1, pdf_page: 7 },
      { label: "1.1", level: 1, print_page: 2 },
    ],
    { "1": 7, "2": 8 },
  );
  assert.match(html, /kb-toc-node--branch is-collapsed/);
  assert.match(html, /kb-toc-caret/);
  assert.match(html, /kb-toc-children/);
  assert.match(html, /kb-toc-node--leaf/);
  assert.equal((html.match(/class="kb-toc-caret"/g) || []).length, 1);
});
