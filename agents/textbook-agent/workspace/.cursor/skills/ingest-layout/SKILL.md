---
name: ingest-layout
description: 六源摄取：把 MinerU 六份产物（layout 主源 + model/v1/v2 辅源）摄取成 textbooks/<书>/ir/pages/NNNN.json。修 IR 线内容前先跑它。不写 pages/、mid/、v2md/。
---

# 六源摄取（IR 线源头）

工具：`ingest_layout(book_id, page_no=0, apply="scan"|"fix", root="")`（`workspace/tools/ingest_layout.py`）。

- `scan` 只统计（块数/kind 分布/页注/行组图/latex 联结数），不写盘；`fix` 写 `ir/pages/NNNN.json`，重摄取换 ID 时写 `ir/remap.json`。
- 幂等：同一份 MinerU 产物重复跑产出逐字节相同。
- 数据源与分工见 `workspace/ir/schema.md`（选源矩阵）；栏目框词表在 `workspace/ir/box_kinds.yaml`。

## 边界

- **只写 `textbooks/<书>/ir/`**；禁止碰 `pages/`、`mid/`、`v2md/`、`mineru/`（源）。
- 摄取层不做人工订正——订正是 `ir/notes/` 注解层的事（见 `ir-notes` 技能）。
- 区块：`official-pipeline`（官方线）与 `v2md-pipeline` 是另外两条线，别互改工具。
