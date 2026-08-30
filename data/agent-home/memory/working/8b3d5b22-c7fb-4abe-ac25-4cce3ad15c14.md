# 工作笔记
对话：8b3d5b22-c7fb-4abe-ac25-4cce3ad15c14
状态：cancelled
用户任务：教材已导入完成。书号：e385beabe9d54002ac89b2f88cf938ea。
请先读工作区 .cursor/skills/page-refine/SKILL.md 和 .cursor/skills/improve-render/SKILL.md，再按下面顺序调用清洗工具，对这本书做逐步清洗。没有书就说没有；禁止用通识改教材定义或题干。

1. checkpoint：note 写清书号与「导入后清洗前」。
2. 逐页确定性清洗：列出 textbooks/e385beabe9d54002ac89b2f88cf938ea/pages/ 下所有 NNNN.md，对每一页调用 refine_page(book_id=e385beabe9d54002ac89b2f88cf938ea, page_no=页码)，去残留 JSON/HTML、收空白。
3. 整书渲染扫描：improve_render(book_id=e385beabe9d54002ac89b2f88cf938ea)，只扫不改；看返回的 by_kind 与 next_steps。
4. 按 next_steps 分层修，不要混。
5. 抽子目录：toc_extract(book_id=e385beabe9d54002ac89b2f88cf938ea)。它先看原文 PDF 目录页，再读 toc.json，然后一次写入 toc_agent.json；不要每个区间另开对话，也不要手写旁路。
6. 重抽目录：build_toc(book_id=e385beabe9d54002ac89b2f88cf938ea)。
7. 再扫一次 improve_render(book_id=e385beabe9d54002ac89b2f88cf938ea)。不要把整页正文贴进对话。到此结束。
停住原因：stopped
