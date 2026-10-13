# RSI 记忆

## 目标

中间表示墨稿上的公式：和旁边汉字一条线，伸出的一笔（上划线、并集杯口）不要被框裁掉。

## 未完成

1. 磁盘 theme.css 已改 overflow:visible，线上仍吃 .last-good（第 121 行还是 hidden）。先 reload_surfaces，再对照第 18 页例12 第二行和独立行。
2. 第 18 页例12 + 三道独立公式对照用户附图验收。

## 已过关

- 2026-09-21：全书字号表 + 行距表已挂到读页。
- 2026-09-21：标题过大判定、按框封顶、框内竖直居中；练习落到 title-3。
- 2026-09-21：公式画高三档；矮框行间改行内体并按框高÷hEm 收字号。
- 2026-09-21：行内 mjx 改为 display:inline。
- 2026-09-21：公式基线。mjx-container 写 vertical-align:-深度em；theme 去掉 middle。例12 第一行 U= A= 已和「全集」「集合」一条线。

## 教训

- 画高不超过框 ≠ 和汉字一条线。对照只验撑破会验错。
- MathJax 要靠 vertical-align:-深度em；middle 会盖掉基线。
- 金样不更新会掉回 .last-good。
- 只改 workspace theme.css 不够：线上读的是 .last-good，不 reload_surfaces 画面还是旧裁切。
