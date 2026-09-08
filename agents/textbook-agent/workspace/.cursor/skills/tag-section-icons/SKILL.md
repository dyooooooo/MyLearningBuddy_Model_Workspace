---
name: tag-section-icons
description: 语文教材栏目图标与标题（学习提示等）收成同一行。中间文件写 ::: kb-section-icon，不要写 HTML。
---

# 栏目图标标题

原文把小圆标贴在「学习提示」左侧，和标题同一行、靠左置顶，右侧一条点线。用 `tag_section_icons(book_id)`，不要手写一书。

中间文件只写围栏：

```text
::: kb-section-icon
![插图](...)

## 学习提示
:::

正文……
```

源页仍是官方 Markdown：图和 `## 学习提示` 分行，标题上下各空一行。点线、赤褐色、图标缩小、栏下首段缩进由 page-body 读页时画。不要把 `class`、点线或全角空格写进书页。

闭栏后必须空一行，否则正文会粘在 `:::` 上，不成段、也不缩进。

## 何时调

- 语文版式任务之后（事件 `task.chinese-textbook-layout.completed`）。
- 科目是 chinese 且类型是 textbook。
- 没有「图标紧挨栏目标题」就停。英语、数学不调。

## 工具已经做的

- 认学习提示、单元学习任务、阅读指导等栏目前的一张图（可带 `kb-center`）。
- 写成 `::: kb-section-icon`，经闸门 `write_mid`，印章一起更新。
- 封面、单元扉页、课文插图不收。

## 不要做

- 不要改 `tag_chinese_layout` / `build_toc` / `improve_render`。
- 不要对目录页 unwrap。
- 不要把课文大图或单元导语图收进栏目标题。
- 不要贴整页正文。
- 不要直接改 `mid/*.md`（不走闸门会被版式任务冲掉）。
