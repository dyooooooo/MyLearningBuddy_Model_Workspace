---
name: kb-three-lines
description: 知识库三线并存总图：老线 pages/mid、新线 v2md、官方线 layout.json 按页出稿。动任何一条线之前先对这张表，确认改的是哪条线的目录、工具和 surface，别串线。
---

# 知识库三线并存总图

同一批教材有三条并存的读页线。前端对照模式（kb-ui `compare.ts`）可以任意两两并排，
每页用 `?src=` 选线。

## 三线一览

| | 老线（旧稿） | 新线（新稿） | 官方线（官方稿本） |
|---|---|---|---|
| `src` 参数 | `old`（**默认**，生产走这条） | `v2md` | `official` |
| 正文来源 | `textbooks/<id>/pages/NNNN.md`（人工可编辑）+ `mid/NNNN.md`（`::: kb-*` 围栏派生层） | `textbooks/<id>/v2md/pages/NNNN.md`，附 `index.md` / `book.md` | 请求时从 `mineru/layout.json` 该页 `para_blocks` 出稿（缺版式才用 v1 清单），**不落盘** |
| 生产者 | 导入流水线（MinerU v1 → `prose.py` 清洗 → 写 pages + mid），之后人工编辑 pages | `v2md_generate.py` 读 MinerU V2 `*_content_list_v2.json` 整本生成 | `official_slice.py` 只读出稿 |
| 一致性机制 | `page_sync` + `mid/.stamps/` 校验和 + conflict 留档 | 无（一次性派生，重新生成即覆盖） | 无（纯函数） |
| GET 写盘 | 写 mid / stamp，**不写正文** | 零写盘 | 零写盘 |
| 渲染插槽 | `surfaces/page-body/`（手写正则 + remark bundle，`profile.yaml` 可切 `legacy`） | `surfaces/page-v2/`（callout 转换、双链） | `surfaces/page-official/`（只压空行，近裸文本） |
| 文本形态 | 干净 Markdown；块 ID 是 `^kb-xxxxxx`（随机） | frontmatter、`[[NNNN]]` 双链、`^kb-title-2` 语义块 ID、`> [!header]` 等 callout（页眉/页脚/页码/脚注），图片是 MinIO 绝对 URL | MinerU v1 原始 Markdown，带 HTML 标签残留，图片相对路径 |
| 可编辑性 | **可编辑，是唯一事实来源，唯一喂 AI 抽取/RAG** | 不可编辑（改了会被 regenerate 覆盖） | 无文件可编辑 |
| 实测覆盖 | 51 本 / 4913 页 | 54 本 / 5307 页 | 51 本（依赖 `mineru/full.md`） |
| 边界技能 | `page-layers` | `v2md-pipeline` | `official-pipeline` |

## 别串线（硬边界）

- 老线治理（清洗、同步、打标）只许碰 `pages/`、`mid/`、`page-body`、`source_text` 等老线件；
  禁止借 `v2md/` 或 `mineru/full.md` 的定位词去改老线页。
- 新线只许碰 `v2md/`、`v2md_*` 工具、`page-v2`；官方线只许碰 `official_*` 工具、`page-official`。
  两条新线互不 import，各自技能里写了明细。
- 修渲染各补各的 golden（`page-body` / `page-v2` / `page-official`），`test_surface` 全绿再 `reload_surfaces`。
- 对账发现老线与新线/官方线文本不一致：先默认是**天然差**（老线有人工订正、有损清洗丢页眉页脚；
  新线保留页眉页脚但无人工订正），不要当成同步 bug 去修。

## 定位

老线 = 生产；新线、官方线 = 评估/对照线（评 MinerU V2 质量、对照原图校对）。
**没有迁移计划之前，新线的任何"改进"都不要回写老线，老线的人工订正也不要手动搬进新线。**
