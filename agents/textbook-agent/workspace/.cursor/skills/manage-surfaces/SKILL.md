---
name: manage-surfaces
description: 改书页、左侧目录树、对照视图的渲染方法，并用金样验证。
---

# 改画面插槽

工作区 `surfaces/` 里是三个画面插槽，不是整页前台。壳子（右栏对话、登录、导入、分栏条）在仓库镜像里，绝对不能提、不能改。

| 插槽 | 改什么 |
|------|--------|
| `page-body` | 书页 Markdown 怎么预处理（目录抬升、裸公式保护、段落、插图原大/同行 `lib/fig-size.mjs`）和 `theme.css` |
| `toc-tree` | 左侧章节列表怎么画 |
| `compare` | 对照图例文案、图在左还是右、`theme.css` |

流程：

1. 先看画面再改 `textbooks/.../pages/*.md`。标题乱、字面出现 `</p>`、目录被吃掉 → 这是渲染问题，改插槽。
2. `list_surfaces` 看当前加载来源（workspace / last-good / seed）和金样。
3. 改算法：`write_surface`，`slot=page-body`，`path=prepare.mjs` 或 `lib/page-toc.mjs` / `lib/fig-size.mjs` 等。插图宽度/同行算法在 `tools/fig_size.py`，见 `manage-figures`。
4. 同步补金样：`tests/goldens/*.in.md` + `*.out.md`（page-body）或 `*.in.json` + `*.out.html`（toc-tree / compare）。
5. `test_surface`。红了不要 `reload_surfaces`——线上仍用上一份通过的。
6. 全绿再 `reload_surfaces`，刷新 `/kb/` 即新画法，不用重建镜像。

未写完的草稿放 `surfaces/<slot>/proposals/`，加载器不执行。

不要写 `/app`、不要提 `agentDock` / `kb-ui`。
