# Skill: 方案调优（edition-tuning）

目标：把「症状」映射到 Bundle 的正确旋钮，永远先试便宜通道。

## 症状 → 旋钮速查

| 症状 | 旋钮 | 字段 |
|---|---|---|
| 主栏短行被判进侧栏 / 侧栏堆顶 | geometry | `aside_left_x1_max` / `aside_right_x0_min` / `aside_max_width` / `min_companion_width` |
| 双栏被并成单栏（或反之） | geometry | `wide_min` / `min_gutter` / `full_width_ratio` |
| 图题没抓到 / 抓错 | patterns | `fig_label` / `figure_caption` |
| 章节/课时切分错 | patterns | `chapter` / `lesson` |
| 目录识别错（点线/页码） | patterns + features | `toc_*` 正则 / `features.toc_style` |
| 「解」「答」角色标错 | patterns | `solution_lead` / `pedagogy_patterns` |
| 页眉页脚残留 | features | `running_header_footer` |
| 大模型改写时的软规则 | policy.entries | 对应 facet 的 must/should 条目 |

## 操作路径（成本从低到高）

1. **改草稿**：`editions.save_draft`，参数 `edition_id` + `patch_json`（点路径→值的 JSON 字符串），如
   `edition_id="x"`, `patch_json='{"config.geometry.aside_left_x1_max": 128}'`
2. **验证**：对问题页 `corpus.reprocess_page`（便宜、秒级）+ `harness.page_diff` + `harness.run_golden`
3. **满意后**才考虑 `editions.publish`（发布才影响后续新导入；已导入页不受影响）
4. 新教材版式差异大 → `editions.fork` 出新方案，别把通用 `_base` 或别的教材方案改坏

## 冷启动（新 PDF）

先 `run_calibrate(edition_id="_base", sample_bboxes=[...])` 拿 geometry 候选，
对照现有方案清单（`get_editions_overview`）判断：复用 / fork。
fork 初值 = calibrate 建议的 geometry + 按目测版式设 features + 常用 patterns 从最像的方案抄。

## 禁区

- 别动 `meta.id` / `meta.version` / `meta.base`（系统管理）
- `subject` 必须与 `grade_band` 匹配
- policy entry 的 `facet` 必须在枚举内；`severity` 用 must/should/may
