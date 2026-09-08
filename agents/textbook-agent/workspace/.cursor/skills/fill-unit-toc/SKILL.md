---
name: fill-unit-toc
description: 有「第N单元」的语文教材：课序号为节、同课篇名并列为小节，只写回 toc.json。目录页交给 write_toc_pages。
---

# 按单元课号补目录

先读 `.cursor/skills/page-layers/SKILL.md`。`build_toc` 不认「第N单元」。这类书在 `build_toc` 之后调 `fill_unit_toc(book_id)`，不要手写旁路，不要改解析器，不要把目录写成 HTML。目录页由后续任务调 `write_toc_pages`。

## 何时调

- 书页里出现「第N单元」，且左栏缺课号、或课号和作品名拼在同一条。
- 导入后独立目录任务里的 `build_toc` 跑完之后；事件 `tool.build_toc` 会再开一轮对话。
- 没有「第N单元」就停。英语、数学等不调。目录页不要在本工具里写。

## 层级（工具已经按这个写）

- lv0：第N单元、后记、目录上无课号的短栏目。
- lv1：纯数字课序号「1」「2」，后面不拼作品名；以及「单元学习任务」、「一 / 二 / 三」活动题。
- lv2：同一课号下的篇名并列（首篇和 *自学一样）。

页码只把印张页留给 `write_toc_pages`。`toc.json` 已经按单元/课号/篇名写好时，本工具不再改 `pages/` 与 `mid/`。

## 不要做

- 不要手写 `<nav class="kb-page-toc">` 或 `<ol class="kb-page-toc">`。
- 不要 `mark_toc_pages`：目录页已是围栏时会收成一行或只剩 Contents。
- 不要对目录页 `improve_render(apply=unwrap)`。
- 补完后不要再调 `build_toc`，不然会盖掉课号层级。
- 不要把一课一诗的书名写进工具。
- 不要贴整页正文。
