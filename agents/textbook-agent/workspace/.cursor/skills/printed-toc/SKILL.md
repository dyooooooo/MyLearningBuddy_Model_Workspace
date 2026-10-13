---
name: printed-toc
description: 通用印刷目录。先读 PDF 文字层，找页用四样信号计分，本地 35B 只补缺。写 toc.print。
---

# 印刷目录小 Agent

工具：`printed_toc_agent(book_id, pages="", force=false, arch="v2")`，文件 `tools/printed_toc_agent.py`。默认走 v2（见下节）；`arch="v1"` 走下面 1–6 步的旧路径。

只调用这一次。不要自己翻 `layout.json`，不要把目录图交给知识库聊天模型。

## 它内部怎么走

1. **缓存**：`toc.print` 里的 `inputs` 指纹（PDF 哈希、PyMuPDF 版本、版面模型版本、torch / transformers 版本、提示词哈希）和这次一样、且没传 `force`，就直接返回旧结果。
2. **找目录页**：四样信号只加不减。MinerU `index` 块 2 分，文字层行尾页码不少于 5 行 1 分，版面服务 `content` 不低于 0.3 得 1 分，35B 看缩略图说是 2 分。满 2 分算目录页，取最长连续段，最多 4 页。版面服务或 35B 失败都算 0 分，不否决。
3. **读事实**：逐页判断。文字层行数和字数够、页面尺寸对得上、至少 80% 的行中心落在 MinerU 块框里，就用文字层。否则整页让 35B 读行。分栏截图默认关（`TOC_CROPS=1` 才开）：沪教英语对着草稿比，条数更差、也更慢。
4. **拼装草稿**：程序按行高、字号、粗细、缩进先拼一条草稿。
5. **35B 终审只提案**：模型可以建议合并、拆开、改级。适用器决定采不采。文字层默认分开，只有续行才并；看图行可以拆。条目头、双页尾、同页兄弟模板三条否决不能越过。改级必须对得上编号、正文标题或文字层缩进，否则留草稿级别。被拒的提案写在 `pages_meta.rejected_proposals`。不读 `printed_toc_read.md`。
6. **补页码、核对**：缺页码的洞仍由 35B 看小块。另报告正文里有、目录里没有的章/节/单元/课标题（`missing_in_toc`，只报告不改条目）。

## 默认路径 `arch="v2"`（10 本标准答案：v2 过 8 本，v1 过 3 本）

第 4、5 步换成 `tools/printed_toc_decide.py`：

- 看图读出的行不再按「猜出来的字号」自动粘；文字层行照旧按真字号粘。
- 每页一次模型调用，输出被 JSON 结构锁住（llama.cpp `json_schema`）：每行答「是不是上一行的下半句、层级、右边有没有印页码、要不要拆」。提示词 `printed_toc_decide.md`。
- Python 只查对所有目录都成立的事实：上一行已有页码就不能再往上粘；不跨栏粘；拆开不能改字；每行都要有答案；层级往下最多深一级。
- 模型想粘的两行，切出这两行放大再问一次（`printed_toc_pair.md`）。两次都说粘、且第二行不像新条目开头，才粘；否则放开采样再问三次：第二行像新条目开头要三票全粘，否则过半就粘。
- 文字层条目的层级一律以印刷样式为准；模型说法不同只标 `needs_review`，不改层级（试过三票改层级：5 次触发 5 次改错，模型错时每次错得一样）。扫描页条目用模型的层级。
- 模型说「右边印了页码」但没读到的行，变成补页码的洞。
- 每条 `entries[].decision` 记下每一步怎么说、谁赢；没定下来的标 `needs_review`。

比对：`python3 /data/agent/eval/printed_toc/stage_report.py --arch v2 --save /tmp/cmp/v2`（`--compare A B` 比两份结果）。

## 产物

`textbooks/<书>/toc.print`。字段包括 `schema_version`、`inputs`、`pages_meta`、`scores`、`evidence`、`missing_in_toc`、`entries`。

不写 `pages/`、`mid/`、`ir/toc.json`，也不占用聊天会话。这一轮不接进 `build_toc`。

写完 `toc.print` 后自动调 `ir_print_toc`，编译成 `ir/print_toc.json`，结构化库的目录页据此画成目录列表；再调 `ir_toc` fix，结构化库左栏以它为骨架重建（见 `ir-toc` 技能）。找页时只有缩略图一票的页，会再用整页大图复核一次（`printed_toc_confirm.md`）。

## 标准答案

只读目录：`eval/printed_toc/<书>.json`（容器里挂在 `/data/agent/eval`）。用 `toc_golden` 比对。
