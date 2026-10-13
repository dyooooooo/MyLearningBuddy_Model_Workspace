---
name: ingest-layout
description: 六源摄取：把 MinerU 六份产物（layout 主源 + model/v1/v2 辅源）摄取成 textbooks/<书>/ir/pages/NNNN.json。修 IR 线内容前先跑它。不写 pages/、mid/、v2md/。
---

# 六源摄取（IR 线源头）

工具：`ingest_layout(book_id, page_no=0, apply="scan"|"fix", root="")`（`workspace/tools/ingest_layout.py`）。

- `book_id=*` 或空 = 全部带 `mineru/layout.json` 的书。
- `scan` 只统计（块数/kind 分布/页注/行组图/latex 联结数），不写盘；`fix` 先删将替换的 `ir/pages/NNNN.json`（全书成功则连残页一起清），再写新文件；重摄取换 ID 时写 `ir/remap.json`。不删 `ir/notes`。
- 幂等：同一份 MinerU 产物重复跑产出逐字节相同。
- 数据源与分工见 `workspace/ir/schema.md`（选源矩阵）；栏目框词表在 `workspace/ir/box_kinds.yaml`。
- 排版：`ir_bands` 在内存里投整页竖轨、给每块写 `slot`（`main` / `aside` / `span`）。树不写盘。阈值在 `workspace/ir/band_rules.yaml`。不要用中点切一刀，不要按书名写死。排障用 `ir_bands.py --book <id> --page N --explain`。画页走墨稿。
- 拿不准的页走 `ir-review`（知识库 Agent），对照原图后只写 `ir/notes`（`replace` / `retitle` / `delete`）。

## 边界

- **只写 `textbooks/<书>/ir/`**；禁止碰 `pages/`、`mid/`、`v2md/`、`mineru/`（源）。
- 摄取层不做人工订正——订正是 `ir/notes/` 注解层的事（见 `ir-notes` 技能）。
- 不要 import `official_*` / `v2md_*`。
- 不要把低置信页交给 35B 或 buddy-api 版式升级。
- 摄取只写内容。字号、行距、折行见 `content-vs-paint`，不要在 Python 里算。
