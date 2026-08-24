# 方案（Edition Bundle）地图

## 先看这张表：哪些字段真的影响 MinerU 导入

改方案之前**必须**先确认改的是不是生效面。写错地方 = 白改。

| 字段 | 对 MinerU 全书导入 | 怎么改 | 何时生效 |
|------|------------------|--------|----------|
| `<edition>/layout_rules.yaml`、`overrides/pages/NNNN.yaml` | ✅ **生效** | `fs.request_edit` 改文件 | 立即（bind-mount + mtime 缓存），`corpus.rebuild_ir` 验证 |
| `config.geometry.*` | ✅ **生效** | `editions.save_draft` | **必须 publish** 才落盘 |
| `config.patterns.*`、`config.features.*` | ✅ **生效**（经 textbook_profile 读取） | `editions.save_draft` | **必须 publish** 才落盘 |
| `assets.theme_css` | ✅ 影响渲染 | 白名单内可直接改 CSS | 立即 |
| `policy.entries` | ❌ **不影响本线** | — | 只在拼装大模型提示词时注入；导入主链永不调用大模型 refine |
| `config.refine_mode`、`config.refine_parallel` | ❌ **不影响本线** | — | 主链是架构上绕开 refine，不是按此字段判断 |
| `config.zones`、`config.figure_group` | ❌ 遗留字段 | — | 已由声明式规则层取代 |

`policy.entries` 并非全无用处：它仍对 **35B 线**的 `b35.vision_layout` 槽位有效。
但在 `import_advice`（MinerU 线）场景里，把规则写进 `policy.entries` 等于没写。

**规则的正确出口**是 `layout_rules.yaml` 三层级联，详见 `rules/40-layout-rules-first.md`：

```text
base_rules.yaml（共享硬不变量，勿改）
  < <edition>/layout_rules.yaml（这本教材的策略）
    < <edition>/overrides/pages/NNNN.yaml（某一页的定点修正）
```

## Bundle 结构（app/services/editions/schema.py）

顶层四段：`meta` / `config` / `policy` / `assets`。

| 段 | 字段 |
|---|---|
| meta | `id`, `version`, `display_name`, `base`(默认 `_base`), `subject`, `grade_band`, `description` |
| config | `refine_mode`(本线不用), `refine_parallel`(本线不用), `geometry`, `patterns`, `features`, `zones`(遗留), `figure_group`(遗留) |
| config.geometry | `page_w, wide_min, aside_max_width, min_gutter, full_width_ratio, min_y_overlap, min_companion_width, aside_left_x1_max, aside_right_x0_min, figure_y_tolerance`（坐标约 0–1000 归一） |
| config.patterns | 正则：`chapter, lesson, fig_label, fig_ref, solution_lead, toc_trailing_page, chapter_toc, toc_section, toc_summary`；`toc_titles[]`；`pedagogy_patterns{}`；`figure_caption` |
| config.features | `columns, running_header_footer, aside, toc_style, figure_policy, math_density` |
| policy | `version` + `entries[]`；entry = `{id, facet, severity(must/should/may), statement, tags[]}` |
| assets | `theme_css`, `glyph_map` |

policy facet 枚举：`layout_geometry, zone_hints, running_chrome, aside_policy, toc_style,
figure_policy, math_density, pedagogy_roles, content_correct, layout_rebuild, unit_judge, vision_layout`。

## 生命周期（重要！）

- **草稿在 Postgres**（`edition_drafts.bundle_json`），不在磁盘 —— 改草稿用 `editions.save_draft`。
- **publish** 才把草稿物化到磁盘 `configs/editions/{id}/`（同时写 `versions/{ts}/` 快照，Postgres `edition_versions` 记录）。
- **fork**：`editions.fork(source_id, new_id)` → 写新种子目录 + 存草稿。
- **rollback**：恢复某版本到草稿并重物化磁盘。
- 磁盘 `configs/editions/` 是 bind-mount。`config.json` 走 API（publish 落盘），
  但 `layout_rules.yaml` / `overrides/pages/` 是**直接改文件**的通道，不经草稿、不需 publish。

⚠️ **`edition.finalize_from_session` 把会话的 `effective_changes` 写进 fork 出的新方案的
`policy.entries`**——那是上面表里的「不影响本线」格。它的价值只是**账本与溯源**，
不要指望它改变下次导入的行为。真要让规则生效，必须另外落到 `layout_rules.yaml`。

## 冷启动标定

`run_calibrate(edition_id, sample_bboxes)`（只读）：输入若干 `[x0,y0,x1,y1]` 样本，
返回 geometry 候选、layout 检测（column_count/main_x0/main_x1/has_left/has_right）、
完整 fork 自 `_base` 的 bundle 草案、`zone_hints` policy 候选。

## Prompt 槽位（app/services/editions/slots.py）

| slot | 线 | 模板文件 | 导入主链会跑吗 |
|---|---|---|---|
| mineru.refine_layout | MinerU | data/prompts/mineru_page_refine_v2.md | ❌ 不跑 |
| mineru.refine_full | MinerU | data/prompts/mineru_page_refine_v1.md | ❌ 不跑 |
| mineru.rerecognize | MinerU | data/prompts/mineru_page_rerecognize_v1.md | ❌ 不跑 |
| mineru.content_correct | MinerU | data/prompts/mineru_page_content_correct_v1.md | ❌ 不跑（mode 已封禁） |
| mineru.layout_rebuild | MinerU | data/prompts/mineru_page_layout_rebuild_v1.md | ❌ 不跑（mode 已封禁） |
| mineru.unit_judge | MinerU | data/prompts/mineru_page_unit_judge_v1.md | ❌ 不跑 |
| b35.vision_layout | 35B | app/import_35b/prompts/layout_blueprint_v6.md | 另一条线 |

六个 `mineru.*` 槽位只在 `reprocess_page` 的已封禁 mode 与 `run_golden_lite` 装配检查里被读到，
**全书导入一个都不跑**。所以改这些模板对导入无效，也别把 `harness.run_golden` 当本线证据。

模板打进镜像 = 昂贵通道：改完必须 rebuild buddy-api + buddy-worker。
