---
name: manage-figures
description: 改教材插图的原大宽度、同行排版和裁切清晰度。算法在 workspace/tools 与 page-body 插槽，不写回 pages/mid。
---

# 改插图插入

先读仓库 `doc/fig-code-ownership.md`。三份文件各管一件事，不要混：

| 要改的事 | 改哪里 | 怎么验证 |
|----------|--------|----------|
| 一张图多宽、哪几张算同一行 | `tools/fig_size.py` | textbook-agent `tests/test_fig_size.py`；刷新 `/kb/` 看页 |
| 图在书页上怎么画（`width%`、`kb-fig-row`） | `surfaces/page-body/lib/fig-size.mjs` + `theme.css` | 金样 `fig-size.*` / `fig-row.*`，`test_surface` 全绿再 `reload_surfaces` |
| 图糊、JPEG 振铃、页预览太糊 | `tools/figure_recrop.py` 里的 `FIGURE_CROP_DPI` / `FIGURE_JPEG_QUALITY` / `PAGE_JPEG_*` | 改完重启 buddy-worker；已导入的书要再导入或让人跑重裁 |

`fig_size.py` 与 `figure_recrop.py` 是库，**不要**写进 `tools/manifest.yaml`。
不要 `import app.*`（buddy-api 容器里没有 textbook-agent 的 app 包）。
不要碰 35B 线的 `import_35b/asset_crop.py`。

宽度只在读页时算，交给画面插槽的 `ctx.figWidths` / `ctx.figRows`。
**不要**把 `style="width:…"` 写进 `pages/*.md` 或 `mid/*.md`。

开关：`TEXTBOOK_FIG_SIZE_BOOKS`（`*` = 全书，空 = 旧的全局 CSS 帽）。不要为一书写死书号进算法。

改 `tools/*.py` 或 `surfaces/*` 须先 `request_code_edit`，等用户在授权卡上点授权。
未写完的画面草稿放 `surfaces/page-body/proposals/`，加载器不执行。
