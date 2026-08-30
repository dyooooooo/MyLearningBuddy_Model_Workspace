import assert from "node:assert/strict";
import test from "node:test";
import { extractPlanMarkdown, looksLikePlan, titleFromMarkdown } from "./planExtract.ts";

test("extracts plan from createPlan JSON", () => {
  const md = extractPlanMarkdown([
    {
      title: "createPlan completed",
      detail: JSON.stringify({ plan: "# TOC 优化\n\n1. 停造小节\n" }),
    },
  ]);
  assert.equal(titleFromMarkdown(md), "TOC 优化");
  assert.match(md, /停造小节/);
});

test("extracts truncated createPlan JSON", () => {
  const body = "# TOC 优化：印刷目录为权威\n\n" + "步骤说明。".repeat(80);
  const raw = JSON.stringify({ plan: body }).slice(0, 2000);
  const md = extractPlanMarkdown([{ title: "createPlan completed", detail: raw }]);
  assert.ok(md.startsWith("# TOC 优化"));
  assert.ok(looksLikePlan(md));
});

test("short teaser is not a plan", () => {
  assert.equal(looksLikePlan("已写好计划，请确认。"), false);
});
