# 方案（Edition Bundle）地图

## Bundle 结构（app/services/editions/schema.py）

顶层四段：`meta` / `config` / `policy` / `assets`。

| 段 | 字段 |
|---|---|
| meta | `id`, `version`, `display_name`, `base`(默认 `_base`), `subject`, `grade_band`, `description` |
| config | `refine_mode`, `refine_parallel`, `geometry`, `patterns`, `features`, `zones`(遗留), `figure_group`(遗留) |
| config.geometry | `page_w, wide_min, aside_max_width, min_gutter, full_width_ratio, min_y_overlap, min_companion_width, aside_left_x1_max, aside_right_x0_min, figure_y_tolerance`（坐标约 0–1000 归一） |
| config.patterns | 正则：`chapter, lesson, fig_label, fig_ref, solution_lead, toc_trailing_page, chapter_toc, toc_section, toc_summary`；`toc_titles[]`；`pedagogy_patterns{}`；`figure_caption` |
| config.features | `columns, running_header_footer, aside, toc_style, figure_policy, math_density` |
| policy | `version` + `entries[]`；entry = `{id, facet, severity(must/should/may), statement, tags[]}` |
| assets | `theme_css`, `glyph_map` |

policy facet 枚举：`layout_geometry, zone_hints, running_chrome, aside_policy, toc_style,
figure_policy, math_density, pedagogy_roles, content_correct, layout_rebuild, unit_judge, vision_layout`。

## refine_mode 语义

`skip`（整书导入默认，不走 35B）→ `layout_only` → `content_correct` → `full` → `rerecognize`。
policy.entries 主要在走大模型 refine 时注入 prompt；`skip` 下大多惰性。

## 生命周期（重要！）

- **草稿在 Postgres**（`edition_drafts.bundle_json`），不在磁盘 —— 改草稿用 `editions.save_draft`。
- **publish** 才把草稿物化到磁盘 `configs/editions/{id}/`（同时写 `versions/{ts}/` 快照，Postgres `edition_versions` 记录）。
- **fork**：`editions.fork(source_id, new_id)` → 写新种子目录 + 存草稿。
- **rollback**：恢复某版本到草稿并重物化磁盘。
- 磁盘 `configs/editions/` 是 bind-mount（便宜通道）；但正确入口是 API，不要手改 seed 文件。

## 冷启动标定

`run_calibrate(edition_id, sample_bboxes)`（只读）：输入若干 `[x0,y0,x1,y1]` 样本，
返回 geometry 候选、layout 检测（column_count/main_x0/main_x1/has_left/has_right）、
完整 fork 自 `_base` 的 bundle 草案、`zone_hints` policy 候选。

## Prompt 槽位（app/services/editions/slots.py）

| slot | 线 | 模板文件 |
|---|---|---|
| mineru.refine_layout | MinerU | data/prompts/mineru_page_refine_v2.md |
| mineru.refine_full | MinerU | data/prompts/mineru_page_refine_v1.md |
| mineru.rerecognize | MinerU | data/prompts/mineru_page_rerecognize_v1.md |
| mineru.content_correct | MinerU | data/prompts/mineru_page_content_correct_v1.md |
| mineru.layout_rebuild | MinerU | data/prompts/mineru_page_layout_rebuild_v1.md |
| mineru.unit_judge | MinerU | data/prompts/mineru_page_unit_judge_v1.md |
| b35.vision_layout | 35B | app/import_35b/prompts/layout_blueprint_v6.md |

模板打进镜像 = 昂贵通道：改完必须 rebuild buddy-api + buddy-worker。
