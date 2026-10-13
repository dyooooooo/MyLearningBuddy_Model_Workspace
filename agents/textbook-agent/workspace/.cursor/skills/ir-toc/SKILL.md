---
name: ir-toc
description: 新线 TOC（结构化库左栏）：有印刷目录 toc.print 就照老线做法拿它当骨架、正文定页并挂细目；没有才从中间页标题块收到最小一级。写 ir/toc.json。review 交给知识库 Agent 逐条核对后写回。不写人工 toc.json。
---

# 新线 TOC 处理器

工具：`ir_toc(book_id, apply="scan"|"fix"|"review", root="", force=False)`（`workspace/tools/ir_toc.py`）；库函数 `build_toc(book)`。

## 有印刷目录（`toc.print` 的 `found` 为真）：老线做法

- 骨架：`toc.print` 每一行原样当条目（标题、层级、是不是章、印刷页、待人工看）。章、节只从印刷目录来，不从正文猜。
- 定页（每行记 `page_from`）：
  - `body`：印刷页 + 偏移那一页的标题块里比到了原字。
  - `near`：前一页、后一两页比到。
  - `offset`：都没比到，就用印刷页 + 偏移（偏移取 `toc.print` 的 `page_offset`，没有就按比到的行投票）。
  - `search`：没印刷页的行，在前后两行的页之间比到。
  - `next` / `prev`：还是找不到时，下面挂着更深一级的行（大标题）跟下一行，普通行跟上一行。
- 比原字：去掉空白和标点、不分大小写；相等，或短的一方至少 4 个字且被包含。不写书名、不写栏目名单。
- 挂细目：从这一行的页到下一行开始之前，没进骨架的正文标题挂在下面，层级 + 1，标 `from_body`。同一句话全书只留第一次（重复的书眉不会每页挂一次）。栏目名、练习题干也会挂进来，这是老线做法本来就有的噪声。
- 文件带 `source = ir_toc_print` 和 `print_sha`（`toc.print` 的指纹）。`printed_toc_agent` 写完 `toc.print` 会自动调一次 `fix`。

## 没有印刷目录：收标题块

- 条目来自中间页里每一级标题块，一直收到最小一级。同一句话只留第一次出现。印着「目录」的那几页整页跳过，不从目录正文行拆条目。
- 能点的页就是标题块所在的中间页。
- 栏目小框和分栏收口块（`kb-close-*` / `kb-algo-*`）不收。版权行（开本、印张、ISBN）不收。
- 草稿里会带上练习、例题。删它们是 `review` 的事，不要在生成时按书名过滤。

## 写盘与覆盖

- `fix` 写 `ir/toc.json`。`reviewed` 为真时默认不覆盖；例外是新稿是印刷目录骨架、而审过的那份不是同一个 `print_sha`。`force=True` 总是重写。
- 来源变了（收标题块 / 审过 / 润色过 → 骨架）时，旧文件先备份到 `logs/toc-backup-书号.json`（和 `polish` 同一个位置），拷回去就恢复。
- `review` 把每一项和该页标题块交给知识库 Agent 核对。练习、例题删掉，错字和层级改掉，写回同一份文件并标 `reviewed`。
- `polish` 仍按收标题块重来，会覆盖骨架；有印刷目录的书不要再跑它。

## 边界

- 老线 `build_toc.py`、`toc.json`、`toc_printed.json` 冻结：只借做法，不调用、不导入。人工 `toc.json` 只作对照，永不改写。
- `toc.print` 哪里错，左栏就哪里错；改目录内容去改 `toc.print` 那条线，不要在这里补。
