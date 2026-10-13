---
name: kb-three-lines
description: 知识库读页线总图：老线 pages/mid（冻结）+ 中间表示（IR）。v2md/官方线已退役。动任何一条线之前先对这张表，别串线。
---

# 知识库读页线总图

同一批教材有两条读页线。前端对照（kb-ui `compare.ts`）只并排：旧稿 | 中间表示、中间表示 | 原图、旧稿 | 原图。

## 两线一览

| | 老线（旧稿） | 中间表示（IR） |
|---|---|---|
| 入口 | `GET /v1/library/…/markdown`（`src=old`，默认） | `GET /v1/ir/…/markdown` |
| 正文来源 | `textbooks/<id>/pages/NNNN.md` + `mid/` | `textbooks/<id>/ir/pages/NNNN.json` + `ir/notes` |
| 生产者 | 导入流水线（冻结后不再改） | `ingest_layout`：layout 主源 + model/v1/v2 辅源；`ir_bands` 给块写 `slot`，树不写盘；画页走墨稿 |
| GET 写盘 | 写 mid / stamp，不写正文 | 缺页或 layout 哈希变了才写 `ir/pages` |
| 渲染插槽 | `surfaces/page-body/` | `surfaces/page-ir/`（字号、折行等显示只在这层 JS） |
| 可编辑性 | 冻结参照；曾是唯一人工源 | 人工订正只写 `ir/notes` |
| 印刷目录页 | 老线 `toc.json` + page-body `liftPageToc` | `ir_print_toc` 把 `toc.print` 编译成 `ir/print_toc.json`，page-ir 画成目录列表（样式照老线，类名 `kb-ir-toc`） |
| 左栏目录树 | 老线 `build_toc` → `toc.json` | `ir_toc` 照老线做法：`toc.print` 当骨架、正文标题块定页并挂细目 → `ir/toc.json`；没有印刷目录才收标题块 |
| 边界技能 | `page-layers` | `ingest-layout` · `ir-bands` · `ir-notes` · `ir-toc` · `ir-review` |

`src=v2md` 与 `src=official` 返回 410。`v2md/` 与 `page-v2` / `page-official` 目录留作考古，library 不再调用。

## 别串线（硬边界）

- 老线治理只许碰 `pages/`、`mid/`、`page-body`、`source_text` 等老线件。
- 中间表示只许碰 `ir/`、`ir_*` / `ingest_layout`、`page-ir`。禁止 import `official_*` / `v2md_*`，禁止再调 `page-body`。
- 修渲染各补各的 golden，`test_surface` 全绿再 `reload_surfaces`。
- 内容归 Python、显示归 `page-ir` JS，见 `content-vs-paint`。禁止字号/折行进 Python，禁止摄取/目录进前端。
- 对账发现老线与中间表示文本不一致：先默认是天然差（老线有人工订正），不要当成同步 bug 去修。
