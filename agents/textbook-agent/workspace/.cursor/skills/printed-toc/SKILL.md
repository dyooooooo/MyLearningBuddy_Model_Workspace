---
name: printed-toc
description: 通用印刷目录。任何书都先扫 model.json，有文字就用词表抽，整页是图才叫本地 35B。写 toc.print。
---

# 印刷目录小 Agent

工具：`printed_toc_agent(book_id)`，文件 `tools/printed_toc_agent.py`。

只调用这一次。不要自己翻 `layout.json`，不要把目录图交给知识库聊天模型。

## 它内部的三步

1. `scan_toc_pages`：只看书前 16 页。`index` 块有至少 3 行，就当文字目录。没有 `index`、几乎没字、但有图的连续页，当图片目录。封面（第 1 页）不算。
2. 文字目录：`parse_lines` 用 `configs/toc_lexicon.yaml`。认出第N章、第N节、第N单元、1.1、A–E、内容提要、复习题、Self-assessment。认不出的行丢掉，不叫模型改字。
3. 图片目录：一页一张图，本地 35B（`127.0.0.1:8081`）。提示词要求先左栏后右栏，色条和页码都要，灰色说明不要。Harness 只留下 level 为 0 或 1、页码是 1–400 的条目。

## 产物

`textbooks/<书>/toc.print`。字段是 `route`（`lexicon` 或 `vision`）、`toc_pages`、`entries`、`timing_s`。

不写 `pages/`、`mid/`、`ir/toc.json`，也不占用聊天会话。
