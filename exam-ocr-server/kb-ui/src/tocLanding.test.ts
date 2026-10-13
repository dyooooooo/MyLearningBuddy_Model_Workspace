import assert from "node:assert/strict";
import test from "node:test";
import { landingPage } from "./tocLanding.ts";

test("landingPage opens the first TOC entry that has a file page", () => {
  assert.equal(
    landingPage([
      { level: 0, is_chapter: true, pdf_page: null },
      { level: 2, is_chapter: false, pdf_page: 4 },
      { level: 0, is_chapter: true, pdf_page: 8 },
    ]),
    4,
  );
});

test("landingPage falls back to the first paged entry", () => {
  assert.equal(landingPage([{ level: 1, pdf_page: 12 }, { level: 1, pdf_page: 20 }]), 12);
});

test("landingPage stays on the cover when nothing is paged", () => {
  assert.equal(landingPage([]), 1);
  assert.equal(landingPage([{ level: 0, is_chapter: true, pdf_page: null }]), 1);
});

test("landingPage does not skip ahead to a later chapter", () => {
  assert.equal(landingPage([{ level: 1, pdf_page: 5 }, { level: 0, pdf_page: 8 }]), 5);
});
