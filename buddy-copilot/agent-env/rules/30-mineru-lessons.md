# MinerU 语料流水线禁区（摘自 doc/mineru-corpus-pipeline-lessons.md）

涉及 MinerU 导入、分栏、侧栏（aside）、目录（TOC）、页眉、reprocess 的任何改动前，
**必须先 Read** `doc/mineru-corpus-pipeline-lessons.md`（问题模式 A–N 全表 + 已落地修改清单）。

## 速记禁区

- **侧栏禁止 `position:absolute`**：落位用文档流 `margin-top`（`--lb-gap`）。
- 含中日韩（CJK）文字的整段**禁止**当裸 LaTeX 处理；走 `normalize_plain` 拆正文+行内公式。
- 有列表编号（list marker）时禁止 `_looks_like_bare_latex`。
- 同角色多个页眉块按 bbox 合并，勿只保留第一条。
- 目录用 `type` / `pedagogy_role` 分流 + 专用渲染器，不当普通 table/text。
- 整书导入不走 35B（`refine_mode=skip`）；单页重识别不走 MinerU——两条线不许混。

## 一句话原则

> Cloud 字段要吃全，版式角色要分清，几何分栏要保守（伴生+文档流落位），
> 预览交互靠 data 属性，改完镜像要重建、整书要 reprocess。

改完发现新坑：把模式补进 lessons 文档第 1 节表格，保持它是唯一活文档。
