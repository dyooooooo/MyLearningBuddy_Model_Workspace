---
name: v2md-pipeline
description: 已退役的 V2 Markdown 线。接口 src=v2md 返回 410。磁盘 v2md/ 只作考古，不要再接到读页。
---

# V2 Markdown 线（已退役）

> 2026-09-12 起：新稿不再是活读页线。`GET …/markdown?src=v2md` 返回 410。
> 工具 `v2md_generate` 标 retired，不进 `load_tool_specs()`。库函数单测可作退役回归。

对照改看「旧稿 | 中间表示」或「中间表示 | 原图」。新书走 `ingest_layout` + `ir_toc`，不要再生成 `v2md/`。
