---
name: mineru-ocr
description: 旁路重跑 MinerU。首次导入仍由前端直连，不要用本技能接任务。
---

# MinerU 旁路

首次导入由前端直连 buddy-api，不要改那条路径，不要给本技能建触发任务。

需要重跑或补页时用这三个工具，全部 HTTP 调 buddy-api：

- `mineru_status(book_id)`：读本地 `meta.json` 与 `mineru/manifest.json`。
- `mineru_dump(book_id, page_ranges)`：只重跑官方 JSON，不改 `pages/`。页范围写成 `6-12`。
- `mineru_convert(book_id)`：整书重新入队识别。不要拿它指定页。

## 何时重跑

- 前三页糊、某几页全空、表格整页丢失。
- 只补几页用 `mineru_dump`，不要整书 `mineru_convert`。

## 不要做

- 不要在导入任务里调这三个工具。
- 不要用本技能改目录或版式。
