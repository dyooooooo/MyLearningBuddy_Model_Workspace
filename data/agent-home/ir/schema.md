# Canonical IR：五源摄取 schema

> 状态：v6（2026-09-16）。本文是中间表示（IR）线的设计文档，放 workspace 里让 Agent 自己维护。
> 摄取器：`workspace/tools/ingest_layout.py` + `workspace/tools/ir_lib.py` + `workspace/tools/ir_bands.py`。
> 产物：`textbooks/<书>/ir/pages/NNNN.json`、`ir/remap.json`、`ir/review.json`。
> 插图文件在 `mineru/images/`，不另作摄取源；渲染时改写成资产 URL。

## 一、五源选源矩阵（实测依据）

| 字段 | 主源 | 辅源/互证 | 说明 |
|---|---|---|---|
| 正文骨架、行结构、span 类型 | layout `para_blocks` | — | 行结构只有它有 |
| 页眉/页码/页注 | layout `discarded_blocks` | — | 带 index，可还原全页阅读序 |
| OCR 置信度 score | layout span `score` | model detection `score` | span 没有时用 detection 补 |
| 公式 LaTeX | model `latex` | — | 按页+bbox 邻近联结，联不上留空 |
| 表格 HTML | v1 `table_body` | layout table span html | 按 bbox 中心距配，不 zip |
| 标题层级 | layout `level` | v2 `content.level` 互证 | 冲突只打 `level_conflict`，骨架仍用 layout |
| 图题 | layout 图片子块（image_caption 等） | v1 题注按 bbox 中心距配 | 子块优先 |
| 图片显示宽度 | layout 块 bbox ÷ page_size | — | 排版真值，写入 `width_frac` |
| 参考文献栏 | 类型 `ref_text` | — | 不用侧栏坐标猜测 |
| 栏目框 | 小宽 title + 栏目词表 | — | 词表 `workspace/ir/box_kinds.yaml` |

不把 `full.md` 当源。六份文件里没有字体名字段（如实记录）。

## 二、每页 JSON schema

```json
{
  "page": 14,
  "book": "1efec97d…",
  "page_size": [595, 841],
  "source": {"layout": "sha16", "model": "sha16", "v1": "sha16", "v2": "sha16",
             "mineru": "3.4.4", "schema": 6},
  "layout": {"format": "nested_bands_v3", "folio": "recto",
             "tracks": {"frame": [70, 535], "main": [206, 535], "aside": [70, 173], "aside_side": "left"},
             "tree": {"id": "page", "kind": "rows", "children": []}},
  "blocks": [ { …块… } ],
  "chrome": {"headers": ["氢原子结构模型"], "page_numbers": ["1.1", "9"],
             "footers": [], "footnotes": [{"order": 10, "text": "① a.u. …"}]}
}
```

### 块

```json
{
  "id": "kb-3f9a1c",
  "type": "text|title|image|chart|table|interline_equation|code",
  "kind": "body|ref|box|inline_image|figure",
  "box_kind": "拓展延伸",
  "order": 4,
  "level": 2,
  "level_conflict": 1,
  "bbox": [56.0, 239.0, 124.0, 254.0],
  "lines": [[{"t": "text", "c": "处于"}, {"t": "eq", "c": "2\\mathrm{p}"}, {"t": "sub", "c": "x"}]],
  "text": "处于$2\\mathrm{p}$轨道上的电子",
  "caption": "图1.7　氢原子的能级图",
  "asset": "92b3….jpg",
  "table_html": "<table>…",
  "width_frac": 0.32,
  "score": 0.97,
  "latex": "\\Delta E = …",
  "row": 0,
  "cross_page": false
}
```

`kind` 判定规则：
- `ref`：type 为 `ref_text`（MinerU 类型，不用 x0 猜测侧栏）；
- `box`：title 且 width_frac < 0.35 且命中栏目词表前缀；
- `inline_image`：image/chart 且块高 < 8% 页高且与文本块阅读序相邻；渲染时并入邻行；
- `figure`：image/chart 且非 inline_image；同 y 重叠的 figure 共享 `row`，渲染为带 `width_frac` 的 HTML 行组；
- 其余 `body`。

排版（`ir_bands`）：坐标一律 PDF 点。先揭页壳，再投**整页栏轨**，按通栏横切、其余按列结构切成横带，落到轨上发格子，格子里再套。成品只有 `layout.tree`（`rows` / `cols` / `flow`）。
树按 HTML 规矩：每个块下标只进一个叶子；父亲的 `members` 是孩子的去重并集，同一数字不写两遍。
文字收口后追加 `kb-close-*` 块，行上带 `eq` / `ieq` / `img` / `sub` / `sup` / `tab` span；图仍走原来的格子。
阈值在 `workspace/ir/band_rules.yaml`，不按书名写死。
拿不准的页由 `ir_review` 交给知识库 Agent，订正只写 `ir/notes`（`replace` / `retitle` / `delete`）。
绘制 `page-ir` 只走 `layout.tree`，公式占位 `data-latex` 由 kb-ui 前端排。

## 三、稳定 ID 与 remap

- 规范化文本 = 全部 span content 去空白拼接。
- `id = sha16(规范化文本 | bbox 网格盐)`，网格 = bbox 各坐标 round 到 10px（抗 OCR 小抖动）。
- 图块无文本 → 用盐 = `asset 名`。
- 重摄取时若块切分变化导致换 ID：`ir/remap.json` 记录 旧 ID → 新 ID（先页内同文，再全文唯一，再页内同 type 近 order）。注解与双链经 remap 保活。

## 四、渲染质量三包（摄取侧责任）

1. **并排图**：figure 块按 y 重叠成行（`row` 字段），题注取子块、缺则按中心距配 v1；输出带宽度百分比的 HTML 行组。
2. **类别驱动**：行内 span `eq` 包 `$…$`；`interline_equation` 包 `kb-ir-eq`，居中交给 `page-ir` 插槽 CSS。
3. **行内小图**：`inline_image` 不单独成段，按 order 并入相邻文本行。
