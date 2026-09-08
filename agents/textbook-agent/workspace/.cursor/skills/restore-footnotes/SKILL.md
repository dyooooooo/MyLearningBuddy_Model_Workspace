---
name: restore-footnotes
description: 从 MinerU 回补语文教材圈号页注，正文上标写成 $^{n}$，注释块上方加横杠。
---

# 回补圈号页注

语文页脚常排两栏。MinerU 把左栏标成 `page_footnote`、右栏常标成正文，转成 pages 后左栏会丢、右栏会断。用 `restore_footnotes(book_id, apply=fix)`，不要手写一书注释。

## 何时调

- 语文版式 `chinese-textbook-layout` 完成之后（事件 `task.chinese-textbook-layout.completed`）。
- 书页或 mineru 里出现 `①〔` / `① 选自` / `① 节选`。
- 没有该形状就停。英语等不调。

## 工具已经做的

- 只认文学页注：圈号 + 〔词〕或选自/节选。
- 右栏被标成正文的半句，接到未写完的同一条。
- 正文圈号、`^{n}` 改成 `$^{n}$`；拉丁词只包上标，不要整词包美元符。
- 注释块上方加横杠。封面和目录页不改。行首活动题的 ① 不收进页脚。

## 不要做

- 不要改 `build_toc`。
- 不要对目录页 unwrap。
- 不要贴整页正文。
