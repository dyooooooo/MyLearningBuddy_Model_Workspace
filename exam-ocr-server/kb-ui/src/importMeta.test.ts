import assert from "node:assert/strict";
import test from "node:test";
import {
  importMetaFromJob,
  parseImportMeta,
  parseImportMetaFromTraceDetail,
  renderIdentifyMessage,
} from "./importMeta.ts";

test("parseImportMeta maps Chinese subject and stage", () => {
  const meta = parseImportMeta({
    label: "普通高中教科书·数学必修第一册",
    textbook_hint: "数学必修第一册.pdf",
    subject: "数学",
    grade_band: "高中",
  });
  assert.deepEqual(meta, {
    label: "普通高中教科书·数学必修第一册",
    textbook_hint: "普通高中教科书·数学必修第一册",
    subject: "math",
    grade_band: "hs",
    category: "textbook",
  });
});

test("importMetaFromJob reads propose_import_meta JSON detail", () => {
  const meta = importMetaFromJob({
    trace: [
      { title: "sweep", detail: "" },
      {
        title: "propose_import_meta",
        detail: JSON.stringify({
          book_id: "b1",
          label: "英语必修第一册",
          textbook_hint: "普通高中教科书·英语必修 第一册",
          subject: "english",
          grade_band: "hs",
          category: "考纲",
        }),
      },
    ],
  });
  assert.deepEqual(meta, {
    label: "英语必修第一册",
    textbook_hint: "普通高中教科书·英语必修 第一册",
    subject: "english",
    grade_band: "hs",
    category: "syllabus",
  });
  assert.equal(parseImportMetaFromTraceDetail("not-json"), null);
});

test("parseImportMeta rejects empty label", () => {
  assert.equal(parseImportMeta({ subject: "math" }), null);
});

test("renderIdentifyMessage fills book tokens", () => {
  const out = renderIdentifyMessage(
    "书号：{{book_id}}。文件：{{filename}}。\n{{preview}}",
    "abc",
    "a.pdf",
    "普通高中教科书 英语",
  );
  assert.equal(out, "书号：abc。文件：a.pdf。\n普通高中教科书 英语");
});

test("renderIdentifyMessage embeds MinerU preview and forbids open_pdf_page", () => {
  const out = renderIdentifyMessage("", "b1", "英语.pdf", "普通高中教科书\n英语必修第一册");
  assert.match(out, /书号：b1/);
  assert.match(out, /英语必修第一册/);
  assert.match(out, /propose_import_meta/);
  assert.match(out, /不要再 open_pdf_page/);
  assert.match(out, /第1册/);
});
