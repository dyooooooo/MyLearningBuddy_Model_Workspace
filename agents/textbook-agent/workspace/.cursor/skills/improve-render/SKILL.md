---
name: improve-render
description: 对整本教材做渲染体检：扫描导入残留与画面缺口，再按层修复（插槽或机械清洗）。
---

# 整书渲染提升

用户说「渲染不好」「导出效果差」「整书优化」「公式识别」「LaTeX 渲染」时走本技能。先读 `.cursor/skills/page-layers/SKILL.md`。

1. 先 `improve_render`（只扫，不改）。`book_id` 可空（三本都扫）、可写书名片段或目录 id。
2. 看返回的 `by_kind` 和 `next_steps`。完整报告在 `traces/improve-render-last.json`。
3. 分层修，不要混：
   - `action=polish`（MinerU JSON、空 `$$`、`$:math$`、HTML 实体、`/ /` 当平行号、公式截图、裸 LaTeX、噪声行、紧跟表格的表截图、未闭合 `$$`、`\text {$...$}` / `\text {$$...$$}`、多项挤在同一对 `$`、填空 `____` 变下标、公式里的裸 `<` `>`）→ 先 `checkpoint`，再 `improve_render(apply='safe')`。
   - `action=unwrap`（源里写回了皮肤 HTML）→ `checkpoint` 后 `improve_render(apply='unwrap')`。拆回官方 Markdown。目录页若已是 `::: kb-page-toc` 不要 unwrap。
   - `action=agent`（`$` 过早闭合、未配对 `$`）→ 打开原版 PDF 对那一页改，工具不自动猜。
   - 画面金样仍难看 → `manage-surfaces`：改 `page-body`，补金样，`test_surface` 全绿再 `reload_surfaces`。
4. 再扫一次，把前后 `issue_count` / `pages_with_issues` 告诉用户。
5. 不要为了「好看」改教材定义或题干；不要把 HTML 写回 `pages/` 或 `mid/`。没有书就说没有。
