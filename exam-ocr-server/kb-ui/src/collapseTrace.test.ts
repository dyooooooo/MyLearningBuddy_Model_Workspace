import assert from "node:assert/strict";
import test from "node:test";
import { collapseTrace, joinThinkFragments, liveStatus, packThinkSegments, toolLabel } from "./collapseTrace.ts";

test("16 reads stay 16 rows", () => {
  const steps = [];
  for (let i = 1; i <= 16; i += 1) {
    const n = String(i).padStart(4, "0");
    const detail = JSON.stringify({ path: `pages/${n}.md` });
    steps.push({ kind: "tool", title: "read running", detail });
    steps.push({ kind: "tool", title: "read completed", detail });
  }
  const { tools } = collapseTrace(steps);
  assert.equal(tools.length, 16);
  assert.equal(toolLabel(tools[0]), "读取 第 1 页");
  assert.equal(toolLabel(tools[15]), "读取 第 16 页");
});

test("search summary shows query", () => {
  const { tools } = collapseTrace([
    { kind: "search", title: "grep running", detail: JSON.stringify({ pattern: "反函数" }) },
    { kind: "search", title: "grep completed", detail: JSON.stringify({ pattern: "反函数" }) },
  ]);
  assert.equal(tools.length, 1);
  assert.equal(toolLabel(tools[0]), "搜索 反函数");
});

test("parallel reads complete by file", () => {
  const a = JSON.stringify({ path: "images/p0005.jpg" });
  const b = JSON.stringify({ path: "images/p0081.jpg" });
  const { tools } = collapseTrace([
    { kind: "tool", title: "read running", detail: a },
    { kind: "tool", title: "read running", detail: b },
    { kind: "tool", title: "read completed", detail: a },
  ]);
  assert.equal(tools.length, 2);
  assert.equal(toolLabel(tools[0]), "对照原图 第 5 页");
  assert.equal(tools[0].status, "completed");
  assert.equal(toolLabel(tools[1]), "对照原图 第 81 页…");
  assert.equal(tools[1].status, "running");
});

test("think fragments join into sentences", () => {
  const joined = joinThinkFragments(["已确认乱码根因：PDF 自定义字体缺少", "ToUnicode 映射。", "接着对照原图。"]);
  assert.deepEqual(joined, ["已确认乱码根因：PDF 自定义字体缺少ToUnicode 映射。", "接着对照原图。"]);
});

test("live status prefers running tool and elapsed time", () => {
  const { tools, thinks, draft } = collapseTrace([
    { kind: "think", title: "思考", detail: "先扫乱码页。" },
    { kind: "tool", title: "read running", detail: JSON.stringify({ path: "images/p0005.jpg" }) },
  ]);
  const status = liveStatus({
    tools,
    thinks,
    draft,
    createdAt: "2026-08-27T07:00:00.000Z",
    now: Date.parse("2026-08-27T07:04:20.000Z"),
  });
  assert.equal(status.headline, "对照原图 第 5 页");
  assert.equal(status.elapsed, "4 分 20 秒");
});

test("think bursts pack into segments and interleave with tools", () => {
  const packed = packThinkSegments(["先定位乱码页。", "不要通读全书。", "接着对照原图。"]);
  assert.deepEqual(packed, ["先定位乱码页。不要通读全书。", "接着对照原图。"]);

  const { timeline, thinks } = collapseTrace([
    { kind: "think", title: "思考", detail: "先定位乱码。" },
    { kind: "think", title: "思考", detail: "不要通读全书。" },
    { kind: "tool", title: "read running", detail: JSON.stringify({ path: "images/p0005.jpg" }) },
    { kind: "think", title: "思考", detail: "第 5 页已认出。" },
  ]);
  assert.equal(thinks.length, 2);
  assert.equal(timeline.length, 3);
  assert.equal(timeline[0].kind, "think");
  if (timeline[0].kind === "think") assert.equal(timeline[0].text, "先定位乱码。不要通读全书。");
  assert.equal(timeline[1].kind, "tool");
  assert.equal(timeline[2].kind, "think");
  if (timeline[2].kind === "think") assert.equal(timeline[2].text, "第 5 页已认出。");
});

test("think pauses or turn words start a new segment", () => {
  const { timeline } = collapseTrace([
    { t: "2026-08-27T07:00:00.000Z", kind: "think", title: "思考", detail: "先看目录页。" },
    { t: "2026-08-27T07:00:10.000Z", kind: "think", title: "思考", detail: "目录英文正常。" },
    { t: "2026-08-27T07:00:20.000Z", kind: "think", title: "思考", detail: "已确认乱码只在附录。" },
  ]);
  assert.equal(timeline.length, 3);
  if (timeline[2].kind === "think") assert.match(timeline[2].text, /已确认/);
});

test("createPlan is not a tool row", () => {
  const { tools } = collapseTrace([
    { kind: "tool", title: "read completed", detail: JSON.stringify({ path: "pages/0008.md" }) },
    { kind: "tool", title: "createPlan completed", detail: JSON.stringify({ plan: "# TOC\n\n1. x\n" }) },
  ]);
  assert.equal(tools.length, 1);
  assert.equal(toolLabel(tools[0]), "读取 第 8 页");
});

test("printed toc traces use Chinese labels", () => {
  assert.equal(toolLabel({ name: "toc_read", status: "running", detail: "" }), "看印刷目录…");
  assert.equal(toolLabel({ name: "toc_locate", status: "completed", detail: "" }), "定位目录页");
});
