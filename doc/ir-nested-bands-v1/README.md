# 已退役：嵌套横带原型

这个目录原来放实验线原型（`containment.py`、`render.py`、`check.py`、`build_canvas.py`、`paint.mjs`、`preview-math.js`、`preview/`、`pages/`）。
算法验完就搬进了教材 Agent 的 workspace，原型整份删除，只留这张指路牌。

| 原来在这里 | 现在在哪 |
|---|---|
| `containment.py`（切带、投栏轨、落格子、文字收口） | `agents/textbook-agent/workspace/tools/ir_bands.py` 的 `assemble_page` |
| `render.py` 的 `to_ir_page`（盒子树 → `layout.tree`） | 同一文件的 `build_layout` / `build_tree` |
| 写死在 Python 顶部的阈值 | `agents/textbook-agent/workspace/ir/band_rules.yaml`（改完不重建镜像） |
| `check.py` 四页对照 | `agents/textbook-agent/tests/test_ir_bands.py` + `tests/goldens/bands/*.json` |
| `paint.mjs` 12 行转调 | 结构线本来就有：`workspace/surfaces/page-ir/paint.mjs` |
| `preview/*.html`、`preview-math.js` | 不需要了。读页走 `GET /v1/ir/{书}/pages/{页}/markdown`，公式由 kb-ui 前端排 |
| `ALGORITHM.md`、`RENDER.md` | `workspace/.cursor/skills/ir-bands/`（`ALGORITHM.md`、`TREE-TO-HTML.md`、`SKILL.md`） |

历次代码审查留在 `doc/claude_kimi/ir-nested-bands-v1-审查-*.md`，讲的是已删除的原型，只作考古。

读页线全图见 [kb-platform-topology.md](../kb-platform-topology.md)「读页线」节。
