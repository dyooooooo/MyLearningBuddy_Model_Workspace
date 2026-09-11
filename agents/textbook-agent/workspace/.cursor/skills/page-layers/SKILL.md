---
name: page-layers
description: 老线（默认 src=old）教材页三层落盘：源 pages 只写官方 Markdown，中间 mid 只写 ::: kb-* 围栏，HTML/CSS 只在读页时生成。改正文目录、居中、列表或书页内容时必读。
---

# 书页三层

> **线界**：本技能只管**老线**（`pages/` + `mid/` + `page-body`，`?src=old`，生产默认）。
> 新线 `v2md/`、官方线 `mineru/full.md` 切片是另外两条独立的线，总图见 `kb-three-lines`，
> 边界见 `v2md-pipeline` / `official-pipeline`——做老线工作时不要动那两线的文件。

每页两份文件，屏幕上的 HTML 不落盘。

| 层 | 路径 | 只写什么 | 禁止 |
|----|------|----------|------|
| 源 | `textbooks/<书号>/pages/NNNN.md` | 官方 Markdown：字、公式、`##` 标题、目录纯文本行 | `class="kb-"`、`<nav>`、`<ol class="kb-page-toc">`、`::: kb-` |
| 中间 | `textbooks/<书号>/mid/NNNN.md` | 同源文字 + `::: kb-page-toc` / `::: kb-center` 等围栏 | 皮肤 HTML、`style=` |
| 显示 | 读页时 `prepare` 把围栏译成 HTML，套 `theme.css` | 不写文件 | 把点线、居中、缩进写回书页 |

改字 → 只改源，闸门会把围栏贴回中间文件。改「这段是目录 / 居中 / 列表」→ 只改中间文件的围栏，闸门会抽字回源。两边不要各写各的。

## 正文目录（人教语文「第N单元」同类书）

左栏以 `toc.json` 为准。正文目录页按同一份条目写：

源（官方 MD）：

```text
目录

第一单元
1
沁园春·长沙 / 毛泽东 9
2
立在地球边上放号 / 郭沫若 11
单元学习任务 36
```

中间文件：把上面整页包进 `::: kb-page-toc` … `:::`。不要手写 `<nav>` / `<ol class="kb-page-toc">`。

层级（工具 `fill_unit_toc` 已经按这个写）：

- lv0：第N单元、后记、目录上无课号的短栏目
- lv1：纯数字课序号「1」「2」，后面不拼作品名；以及「单元学习任务」、一/二/三活动题
- lv2：同一课号下的篇名并列（首篇和 *自学一样）

页码用现在的 PDF 页。`toc.json` 已经按单元/课号/篇名写好时，调 `fill_unit_toc` 只重写三层文件，不要再按正文首现回填页码。

## 不要做

- 不要把抬升后的 HTML 写进 `pages/` 或 `mid/`。插图宽度也不写进这两层，见 `manage-figures`。
- 不要对已是围栏的目录页 `improve_render(apply=unwrap)` 或 `mark_toc_pages`。
- 不要为了一书改 `build_toc` 解析器。
- 不要贴整页正文。
