---
name: ir-notes
description: IR 注解层：ir/notes/NNNN.yaml（replace/retitle/delete，old 值不匹配拒绝套用），渲染时纯叠加不改源。配套试金石 ir_diff 对拍老线 pages。
---

# IR 注解层 + 试金石

工具：`ir_note(book_id, page_no, apply, root)`（`workspace/tools/ir_notes.py`）——校验并渲染「IR + 注解」，返回 rejected 报告。
试金石：`ir_diff(book_id, page_no=0, limit=20, root)`（`workspace/tools/ir_diff.py`）——IR(+注解) 渲染 vs 老线 `pages/NNNN.md`（只读参照），逐页相似度与差异方向。

- 注解条目：`{id, block: kb-<id>, op, old, new, author?}`；`old` 在块内任一 span 精确匹配不上即整条拒绝，**绝不半套用**。
- replace 在 span 层匹配（blk["text"] 是渲染形态，直接匹配会命中 $ 边界导致 text/lines 分叉）。
- 注解是唯一的人工编辑入口：要改正文，写 notes，不改 `ir/pages/*.json`（改了会被重摄取覆盖）。

## 边界

- 不写 `pages/`、`mid/`、`v2md/`；`ir_diff` 对老线只读。
- 块 ID 重摄取后变化时查 `ir/remap.json`（见 `ingest-layout`）。
