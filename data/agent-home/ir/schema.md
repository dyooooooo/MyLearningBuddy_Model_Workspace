# Canonical IR：五源摄取 schema

> 状态：v9（2026-09-22）。本文是中间表示（IR）线的设计文档，放 workspace 里让 Agent 自己维护。
> 摄取器：`workspace/tools/ingest_layout.py` + `workspace/tools/ir_lib.py` + `workspace/tools/ir_bands.py`。
> 产物：`textbooks/<书>/ir/pages/NNNN.json`、`ir/toc.json`、`ir/notes/`、`ir/remap.json`、`ir/review.json`。字号和折行不写盘，由 `surfaces/page-ir` 读中间页现算。
> 插图文件在 `mineru/images/`，不另作摄取源；渲染时改写成资产 URL。
> 画页走墨稿（按块框绝对定位）。树、栏轨、字宽估计不写进页 JSON。

## 一、五源选源矩阵（实测依据）

| 字段 | 主源 | 辅源/互证 | 说明 |
|---|---|---|---|
| 正文骨架、行结构、span 类型 | layout `para_blocks` | — | 行结构只有它有 |
| 页眉/页码/页注 | layout `discarded_blocks` | — | 带 index，可还原全页阅读序 |
| OCR 置信度 score | layout span `score` | model detection `score` | span 没有时用 detection 补 |
| 公式 LaTeX | model `latex` | — | 按页+bbox 邻近联结，联不上留空 |
| 表格 HTML | v1 `table_body` | layout table span html | 按 bbox 中心距配，不 zip |
| 标题层级 | layout `level` | v2 `content.level` 互证 | 冲突只打 `level_conflict`，骨架仍用 layout |
| 图题 | layout 图片子块（image_caption / image_footnote 等，含子块 bbox） | v1 题注按中心距补字（无题注框） | 子块优先；`captions[]` 保框，`caption` 仍是拼接字符串 |
| 图片显示宽度 | layout 块 bbox ÷ page_size | — | 排版真值，写入 `width_frac` |
| 参考文献栏 | 类型 `ref_text` | — | 不用侧栏坐标猜测 |
| 栏目框 | 小宽 title + 栏目词表 | — | 词表 `workspace/ir/box_kinds.yaml` |
| 块位置 `slot` | 内存栏轨 + `_assign_members` | — | `main` / `aside` / `span`；通栏写 `span` |

不把 `full.md` 当源。六份原始文件只给 Python 摄取用。画页只读 `ir/pages`（和日后的 `ir/toc.json`），不读 `mineru/`。字号由画页按中间页行框高度取全书众数；印刷行用中间页的 `lines`。行内公式跟所在行走。

## 二、每页 JSON schema

顶键只留：`page`、`book`、`page_size`、`source`（`schema: 9`）、`chrome`、`blocks`。可选顶键 `folio`（verso / recto）。

**不再写入：** `layout.tree`、`layout.tracks`、`layout.em`、`layout.frame`、`layout.format`；块上的 `align` / `indent` / `indented`（只给已不用的走树 CSS 用）。

```json
{
  "page": 14,
  "book": "66fdd0558d97406bac2b7840c9547165",
  "page_size": [595, 841],
  "folio": "recto",
  "source": {"layout": "sha16", "model": "sha16", "v1": "sha16", "v2": "sha16",
             "mineru": "3.4.4", "schema": 9},
  "blocks": [ { …块… } ],
  "chrome": {"headers": ["集合"], "page_numbers": [{"text": "9", "bbox": [520, 800, 540, 820]}],
             "footers": [], "footnotes": [{"order": 10, "text": "① …"}]}
}
```

### 块

```json
{
  "id": "kb-3f9a1c",
  "type": "text|title|image|chart|table|interline_equation|code",
  "kind": "body|ref|box|inline_image|figure",
  "slot": "main|aside|span",
  "box_kind": "拓展延伸",
  "order": 4,
  "level": 2,
  "level_conflict": 1,
  "bbox": [56.0, 239.0, 124.0, 254.0],
  "angle": 0,
  "merge_prev": false,
  "first_indent": 28.0,
  "line_height": 11.8,
  "lines": [{"bbox": [85.1, 218.7, 526.6, 230.4], "score": 1.0,
             "spans": [{"t": "text", "c": "处于", "bbox": [56.0, 239.0, 80.0, 254.0]},
                       {"t": "eq", "c": "2\\mathrm{p}", "bbox": [80.0, 239.0, 110.0, 254.0]},
                       {"t": "sub", "c": "x"}]},
            {"bbox": [57.7, 239.7, 525.4, 251.5], "score": 1.0, "spans": []}],
  "text": "处于$2\\mathrm{p}$轨道上的电子",
  "caption": "图1.7　氢原子的能级图",
  "captions": [{"type": "image_caption", "text": "图1.7　氢原子的能级图",
                "bbox": [80.0, 510.0, 200.0, 528.0]}],
  "asset": "92b3….jpg",
  "table_html": "<table>…",
  "width_frac": 0.32,
  "score": 0.97,
  "latex": "\\Delta E = …",
  "row": 0,
  "cross_page": false
}
```

`kind` 判定规则（用途，不是位置）：
- `ref`：type 为 `ref_text`（MinerU 类型，不用 x0 猜测侧栏）；
- `box`：title 且 width_frac < 0.35 且去掉末尾冒号后整名等于栏目词表一项；
- `inline_image`：image/chart 且块高 < 8% 页高且与文本块阅读序相邻；渲染时并入邻行；
- `figure`：image/chart 且非 inline_image；同 y 重叠的 figure 共享 `row`，渲染为带 `width_frac` 的 HTML 行组；
- 其余 `body`。

`slot` 判定规则（位置，不是用途）：
- 摄取时在内存里投栏轨，再用 `_assign_members` 同一套门槛：整块落在侧栏轨、且和正文轨空隙大于 `COL_GAP=14` → `aside`；
- 块宽占版心 `SPAN_FRAC`（0.85）以上 → `span`；
- 其余 `main`。
- 第 14 页问句「此处为什么有两种情况？」预期：`type=text`，`kind=body`，`slot=aside`。

墨稿仍读的块字段：`id`、`type`、`kind`、`slot`、`bbox`、`lines`、`text`、`first_indent`、`line_height`、`asset`、`caption` / `captions`、`table_html`、`width_frac`、`level`、`box_kind`、`row`。

排版（`ir_bands`）：坐标一律 PDF 点。先揭页壳，再按整页宽度补空白；Y 重叠分栏。这一步只在内存里发生，给每块写 `slot`。`--explain` 仍可打印带和轨，不写进页文件。
行对象带 `bbox`，span 带 `bbox` / `cross_page`；块上留下 `angle`、`merge_prev`、`lines_deleted`。
`lines` 优先来自 `model.json` 的 `ocr_text` 光学行（点坐标）。检测结果里没有字：layout 的 spans 整段落在第一行，其余行只有框。配不上光学行时仍用 layout 合成行。
`first_indent`：最上行最左 x 减后行最左 x 的众数（点）；小于 8 点不写。`line_height`：各光学行高度的中位数（点）。
贴字定位用块 `bbox`。墨稿字号和行距由画页从中间页现算，同一类型全书一个整数，不写 `ir/fonts.json`。缩进优先用 `first_indent`，否则比行框与块框。标题/正文看 `type`（`level` 定标题层级）。旧页 `lines` 仍可能是 span 数组，读的时候两种都认。
老页里可能还留着 `kb-close-*` 派生块，只作兼容读取，新导入不再生成。
旧页没有 `slot`：画页回退 `isAsideBox` 猜框，不强制立刻重摄取。
阈值在 `workspace/ir/band_rules.yaml`，不按书名写死。
拿不准的页由 `ir_review` 交给知识库 Agent，订正只写 `ir/notes`（`replace` / `retitle` / `delete`）。
绘制 `page-ir` 走墨稿；有 `slot=aside` 且 `kind` 不是 `box` 着侧栏色。公式在 paint 里一次画完（`data-latex-src`）。

## 三、稳定 ID 与 remap

- 规范化文本 = 全部 span content 去空白拼接。
- `id = sha16(规范化文本 | bbox 网格盐)`，网格 = bbox 各坐标 round 到 10px（抗 OCR 小抖动）。
- 图块无文本 → 用盐 = `asset 名`。
- 重摄取时若块切分变化导致换 ID：`ir/remap.json` 记录 旧 ID → 新 ID（先页内同文，再全文唯一，再页内同 type 近 order）。注解与双链经 remap 保活。

## 四、渲染质量三包（摄取侧责任）

1. **并排图**：figure 块按 y 重叠成行（`row` 字段），题注取子块、缺则按中心距配 v1；输出带宽度百分比的 HTML 行组。
2. **类别驱动**：行内 span `eq` 包 `$…$`；`interline_equation` 包 `kb-ir-eq`，居中交给 `page-ir` 插槽 CSS。
3. **行内小图**：`inline_image` 不单独成段，按 order 并入相邻文本行。
