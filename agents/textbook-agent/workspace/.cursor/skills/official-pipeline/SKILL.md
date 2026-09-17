---
name: official-pipeline
description: 已退役的官方稿线。接口 src=official 返回 410。layout.json 只给中间表示摄取当主源。
---

# 官方稿线（已退役）

> 2026-09-12 起：官方稿不再是活读页线。`GET …/markdown?src=official` 返回 410。
> 工具 `official_slice` 标 retired，不进 `load_tool_specs()`。库函数单测可作退役回归。

`mineru/layout.json` 的 `para_blocks` 是中间表示的骨架源，由 `ingest_layout` / `ir_lib` 读取，不要再走 `official_lib` 出稿给 library。
