# Canonical IR：六源摄取 schema v1

> 状态：v1（2026-09-11）。本文是 IR 线的设计文档，放 workspace 里让 Agent 自己维护。
> 摄取器：`workspace/tools/ingest_layout.py` + `workspace/tools/ir_lib.py`。
> 产物：`textbooks/<书>/ir/pages/NNNN.json`、`ir/remap.json`。

## 一、六源选源矩阵（实测依据）

| 字段 | 主源 | 辅源/互证 | 说明 |
|---|---|---|---|
| 正文骨架、行结构、span 类型 | layout `para_blocks` | — | 行结构只有它有 |
| 页眉/页码/页注 | layout `discarded_blocks` | — | 带 index，可还原全页阅读序 |
| OCR 置信度 score | layout span `score` | model detection `score` | span 没有时用 detection 补 |
| 公式 LaTeX | model `latex` | — | 按页+bbox 邻近联结，联不上留空 |
| 表格 HTML | v1 `table_body` | layout table span html、full.md 对拍 | v1 最全 |
| 标题层级 | layout `level` | v2 `content.level` 互证 | |
| 图题 | layout 图片子块（image_caption 等） | v1 `image_caption`/`chart_caption` 按序配对 | 子块优先 |
| 图片显示宽度 | layout 块 bbox ÷ page_size | — | 排版真值，替代老线事后推测 |
| 分栏/侧栏 | 块 bbox x 聚类 | 类型 `ref_text` 直认 | |
| 栏目框 | 小宽 title + 栏目词表 | 几何确认 | 词表 `workspace/ir/box_kinds.yaml` |

六份文件里没有字体名字段（如实记录）；字号只有相对大小可推（span bbox 高度排名）。

## 二、每页 JSON schema

```json
{
  "page": 14,
  "book": "1efec97d…",
  "page_size": [595, 841],
  "source": {"layout": "sha16", "model": "sha16", "v1": "sha16",
             "mineru": "3.4.4", "schema": 1},
  "blocks": [ { …块… } ],
  "chrome": {"headers": ["氢原子结构模型"], "page_numbers": ["1.1", "9"],
             "footers": [], "footnotes": [{"order": 10, "text": "① a.u. …"}]}
}
```

### 块

```json
{
  "id": "kb-3f9a1c",              // sha16(规范化文本 + bbox 网格盐)，见 §三
  "type": "text|title|image|chart|table|interline_equation|code",
  "kind": "body|ref|box|inline_image|figure|figure_cell",
  "box_kind": "拓展延伸",          // kind=box 时
  "order": 4,                     // 全页阅读序（discarded 的 index 参与排序）
  "level": 2,                     // title 才有
  "col": 0, "aside": false,       // 分栏判定
  "lines": [[{"t": "text", "c": "处于"}, {"t": "eq", "c": "2\\mathrm{p}"}]],
  "text": "处于$2\\mathrm{p}$轨道上的电子",   // 拼接成品，给搜索/AI
  "caption": "图1.7　氢原子的能级图",        // 图/表/栏目框标题
  "asset": "92b3….jpg",           // image/chart
  "table_html": "<table>…",       // table
  "width_frac": 0.32,             // image/chart/table 的排版宽度
  "score": 0.97,                  // 块内最低 span score（无则 null）
  "latex": "\\Delta E = …",       // model.json 联结所得（可无）
  "cross_page": false
}
```

`kind` 判定规则：
- `ref`：type 为 `ref_text`，或 x0 > 0.62·页宽且块宽 < 0.38·页宽（侧栏候选）；
- `box`：title 且 width_frac < 0.35 且命中栏目词表前缀；
- `inline_image`：image/chart 且块高 < 8% 页高且与文本块阅读序相邻；
- `figure`：image/chart 且非 inline_image；同 y 区间（容差 15px）的 figure 共享 `row` 号（`"row": 3`），渲染为图排行；
- 其余 `body`。

## 三、稳定 ID 与 remap

- 规范化文本 = 全部 span content 去空白拼接。
- `id = sha16(规范化文本 | bbox 网格盐)`，网格 = bbox 各坐标 round 到 10px（抗 OCR 小抖动）。
- 图块无文本 → 用盐 = `asset 名`。
- 重摄取时若块切分变化导致换 ID：`ir/remap.json` 记录 旧 ID → 新 ID（先按规范化文本精确配，再按同 type 最近 order 配）。注解与双链经 remap 保活。

## 四、渲染质量三包（摄取侧责任）

1. **并排图**：figure 块按 y 重叠成行（`row` 字段），题注取子块、缺则 v1 补配；
2. **类别驱动**：公式三类——行内 span `eq` 包 `$…$`；`interline_equation` 有 latex 走 `$$…$$`，截图型居中定高；`kind` 注册表可扩展；
3. **行内小图**：`inline_image` 不单独成块渲染，按 order 并入相邻文本行。
