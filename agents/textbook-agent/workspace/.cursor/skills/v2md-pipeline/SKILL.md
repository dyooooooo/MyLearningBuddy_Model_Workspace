---
name: v2md-pipeline
description: 隔离的 V2 Markdown 线：只改 v2md/、v2md 工具和新插槽 page-v2。禁止写 pages/、mid/、mineru/full.md，禁止切官方稿。
---

# 隔离 V2 Markdown 线

> **线界**：三线并存（老线 `pages/mid`、本线、官方线 `full.md` 切片），总图见 `kb-three-lines`。

这条线从 MinerU 内容清单 V2（`mineru/*_content_list_v2.json`）生成带 Obsidian 标签的分页稿，并用 `page-v2` 渲染。官方稿归官方线，不在这里切 `full.md`。

## 只许改

| 路径 | 做什么 |
|------|--------|
| `textbooks/<书号>/v2md/` | 分页稿 `pages/NNNN.md`、`index.md`、只读导出 `book.md` |
| `tools/v2md_generate.py` · `v2md_lib.py` | 只生成新稿 |
| `surfaces/page-v2/` | 新稿怎么预处理和上色 |
| `surfaces/compare/profile.yaml` · `paint.mjs` | 对照六档名称 |

## 禁止

- 写 `pages/`、`mid/`、`mineru/full.md`
- 读官方稿或 import `official_*` 去切 `full.md`
- 改 `page-body/prepare.mjs` 或旧 `page-layers` 三层
- 把书规写进 `/app`、buddy-api、kb-ui
- 挂钩全书导入 `process_kb_book`

## 怎么跑

1. `v2md_generate(book_id)` 整本写入 `v2md/`。可选 `page_no` 只写一页。
2. 对照里「新稿」读 `v2md/pages/`，走 `src=v2md` + `page-v2`，不走 `sync_page`。
3. 化学点燃式保持 LaTeX（`\\xlongequal{\\text{点燃}}`），不要截成 `![公式]`。
4. 官方稿走独立的 `official-pipeline` 技能。

改插槽后补 `page-v2` 金样，`test_surface` 全绿再 `reload_surfaces`。
