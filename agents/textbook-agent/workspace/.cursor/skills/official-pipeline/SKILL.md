---
name: official-pipeline
description: 隔离的官方稿线：只读 mineru/layout.json（缺则 v1 内容清单），用 page-official 渲染。禁止读 V2 JSON，禁止写 pages/、mid/、v2md/、mineru/full.md。
---

# 隔离官方稿线

> **线界**：三线并存（老线 `pages/mid`、新线 `v2md`、本线），总图见 `kb-three-lines`。

这条线只服务 MinerU 官方 Markdown。页界来自 MinerU 自己的按页中间表示：`mineru/layout.json` 的 `pdf_info[page_idx].para_blocks`（和生成 `full.md` 的是同一份）。缺 `layout.json` 时才退回官方 v1 内容清单（`*_content_list.json` 的 `page_idx`），仍按页出稿，不在全书 `full.md` 里搜针。页眉页脚页码页脚注在 `discarded_blocks` 里，不进正文。

## 只许改

| 路径 | 做什么 |
|------|--------|
| `tools/official_slice.py` · `official_lib.py` | 只读出稿 |
| `surfaces/page-official/` | 官方稿怎么预处理和上色 |

## 禁止

- 读 `*_content_list_v2.json` 或 import `v2md_*`
- 写 `pages/`、`mid/`、`v2md/`、`mineru/full.md`
- 改 `page-v2/` 或把官方稿送进 `page-v2`
- 挂钩全书导入 `process_kb_book`
- 用全书 `full.md` 做启发式切片

## 怎么跑

1. `official_slice(book_id, page_no)` 现场从该页 `para_blocks` 出稿，不落盘。
2. 对照里「官方稿」走 `src=official` + `page-official`。
3. 官方稿没有页眉页脚页脚注，和原图/新稿对不齐是预期差。

改插槽后补 `page-official` 金样，`test_surface` 全绿再 `reload_surfaces`。
