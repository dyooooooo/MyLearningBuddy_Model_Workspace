---
name: write-toc-pages
description: 按 toc.json 唯一写手落盘目录源页与中间围栏。apply=scan 只统计，apply=fix 才写。
---

# 写目录页

`toc.json` 是目录层级的唯一真值。算层级的工具（`build_toc` / `fill_unit_toc`）只写 `toc.json`。目录页由本工具经闸门落盘。

先读 `.cursor/skills/page-layers/SKILL.md`。只调用一次 `write_toc_pages(book_id, apply=fix)`。

## 何时调

- 「导入后建目录」任务完成之后（非语文教材、课标）。任务挂 `task.import-book-toc.completed`，
  也就是 `build_toc` 与 `toc_extract` 两步都跑完 —— 挂 `tool.toc_extract` 会和建目录并发，
  写到的是中间态 `toc.json`。
- `fill_unit_toc` 之后（语文教材，即使它 skipped 也要写）。
- 审计用 `apply=scan`，不改文件。

## 规则

- 行末只写印张页，不写 PDF 页。
- 印张页不在本册 `print_to_pdf` 里：照原样落盘，不要重算，计入 `unmapped`。
- 只写印刷目录的形态：章（level 0）与节（level 1）总是写；更深的层只有自带印张页
  （`print_page`，说明它本来就印在目录页上）才写。`toc_extract` 从正文扒出来的小节没有
  印张页，不进目录页 —— 它们只走左栏目录树。
- 禁止对目录页 `improve_render(apply=unwrap)`。
- 不要手写 `<nav>` / HTML。围栏体必须等于源页原文。
- 无该形状（没有 `toc_pages`）就报 `empty_toc_pages` 并结束，不要造页。
- 这些规则对以后每本导入都生效。多页目录按**页内最早骨架条**切，不要按该页第一行（续页常先印「附录」栏头）。「附 录」与「附录」视为同一条。切不出就 `split_failed`，不写盘。
- 三道护栏，报出来就是拒写，不要用 `force` 硬顶（只有人明确要求才 `force=true`）：
  - `shrink_guard` —— 写入行数少于该页现有行数的 60%。
  - `grow_guard` —— 写入行数超过现有行数的 3 倍（多页目录被压进一页的典型征兆）。
  - `split_failed` —— 多页目录对不上页内最早骨架条，无法判断哪条归哪页。宁可不动原文。

## 不要做

- 不要再调 `build_toc` 覆盖层级。
- 不要把一书专名写进工具。
- 不要贴整页正文。
