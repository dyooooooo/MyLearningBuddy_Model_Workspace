---
name: ir-toc
description: 新线 TOC 处理器：从 layout 识别目录页、解析「标题 …… 页码」条目、页码块映射印刷页→pdf页，产 ir/toc.json。与人工 toc.json 对拍只报差异，不写人工目录。
---

# 新线 TOC 处理器

工具：`ir_toc(book_id, apply="scan"|"fix", root="")`（`workspace/tools/ir_toc.py`）；库函数 `build_toc(book)`。

- 目录页 = 「目 录/目录/Contents」title + 点线导引 text 块（整页常并成一个 span，按 \n 切分）。
- 印刷页→pdf 页用逐页 discarded 页码块建图，**目录页自身排除**（页脚数字会抢映射）。
- `fix` 写 `ir/toc.json`；人工 `toc.json` 是参照，永不改写。

## 边界

- 层级规则（`_LEVEL_PATTERNS`）只认全书共性形状（第N章/单元/课、X.Y.Z、Unit N、附录/绪言）；一书专名进词表属于违规（见 open-closed 规则）。
- 章首页常不印页码 → `pdf_page=null`，条目置灰不可点，属预期。
