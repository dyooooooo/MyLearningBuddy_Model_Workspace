---
name: page-refine
description: 对照原图逐页清洗教材 Markdown。导入完成后的后台任务会读这份技能。
---

# 逐页清洗

先读 `.cursor/skills/page-layers/SKILL.md`。一次只改一页。改字写 `pages/NNNN.md`（官方 Markdown）；围栏写 `mid/NNNN.md`。原图在消息附件里。

1. 先调用 `refine_page`。
2. 对照原图改公式、题号、侧栏、表格。
3. 原子写回同一文件。不要贴整页正文。
4. 不要占用前台聊天那条 Cursor 会话。
