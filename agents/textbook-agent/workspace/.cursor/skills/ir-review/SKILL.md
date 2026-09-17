---
name: ir-review
description: 中间表示低置信分栏与识别的二次复查。只许知识库 Agent 对照原图、订正写 ir/notes。禁止 refine_page、禁止 35B、禁止 buddy-api 版式升级。
---

# 低置信复查（知识库 Agent）

工具：`ir_review(book_id, apply="scan"|"fix", root="")`。

- `scan` 只统计；`fix` 写 `textbooks/<书>/ir/review.json`。
- 触发：竖轨缺失、带内成员跨轨、收口跨带、图注未吸附、树过深、正文 `score` 低于 0.85。
- 拿到清单后：`open_pdf_page` 对照原图，订正只写 `ir/notes`（`replace` / `retitle` / `delete`，见 `ir-notes`）。改阈值走 `ir/band_rules.yaml`，再 `ingest_layout`。
- 摄取后若返回 `review_pages > 0`，先跑本工具再改栏。

## 边界

- 只写 `ir/review.json`；订正只写 `ir/notes`。
- 禁止写 `pages/`、`mid/`。
- 禁止 `refine_page` / `refine_book`（那是老线）。
- 禁止 35B 视觉 refine，禁止 buddy-api `layout_escalate_*`。
