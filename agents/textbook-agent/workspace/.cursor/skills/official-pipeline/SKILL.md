---
name: official-pipeline
description: 隔离的官方稿线：只读 mineru/full.md 与 v1 内容清单，用 page-official 渲染。禁止读 V2 JSON，禁止写 pages/、mid/、v2md/、mineru/full.md。
---

# 隔离官方稿线

> **线界**：三线并存（老线 `pages/mid`、新线 `v2md`、本线），总图见 `kb-three-lines`。

这条线只服务 MinerU 官方 Markdown（`mineru/full.md`）。页界来自官方 v1 内容清单（`*_content_list.json` 的 `page_idx`），按页号单调往前找、同页取最早命中，不回看，不拿新稿定位词去抠。表格按单元格找；封面短词拼成一句；同一行短于 8 字的假起点丢掉；本页长句走完再让后页开口，避免人名/续表插进未完成的正文；出版社名不当锚；中间缺真正文时上一页收到自己最后一句。

## 只许改

| 路径 | 做什么 |
|------|--------|
| `tools/official_slice.py` · `official_lib.py` | 只读切片 |
| `surfaces/page-official/` | 官方稿怎么预处理和上色 |

## 禁止

- 读 `*_content_list_v2.json` 或 import `v2md_*`
- 写 `pages/`、`mid/`、`v2md/`、`mineru/full.md`
- 改 `page-v2/` 或把官方稿送进 `page-v2`
- 挂钩全书导入 `process_kb_book`

## 怎么跑

1. `official_slice(book_id, page_no)` 现场切 `full.md`，不落盘。
2. 对照里「官方稿」走 `src=official` + `page-official`。
3. 官方稿没有页眉页脚页脚注，和原图/新稿对不齐是预期差。

改插槽后补 `page-official` 金样，`test_surface` 全绿再 `reload_surfaces`。
