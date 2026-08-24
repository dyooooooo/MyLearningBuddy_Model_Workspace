# Skill: 方案调优（edition-tuning）

目标：把「症状」映射到**生效面**上的正确旋钮，永远先试便宜通道。

改之前先看 `context/editions.md` 顶部那张表：方案里只有 `layout_rules.yaml`、
`overrides/pages/`、`config.geometry/patterns/features` 影响 MinerU 导入；
`policy.entries` 与 `refine_mode` 对本线无效。

## 症状 → 旋钮速查

| 症状 | 旋钮 | 字段 / 文件 |
|---|---|---|
| 某一页判错（只有这页） | page override | `<edition>/overrides/pages/NNNN.yaml` |
| 这本教材多页同症状（主栏短行被判进侧栏、侧栏堆顶等） | 教材规则 | `<edition>/layout_rules.yaml` 加一条带 `note` 的规则 |
| 主栏边缘 / 伴生宽度这类**阈值**量错 | geometry | `aside_left_x1_max` / `aside_right_x0_min` / `aside_max_width` / `min_companion_width` |
| 双栏被并成单栏（或反之） | geometry | `wide_min` / `min_gutter` / `full_width_ratio` |
| 图题没抓到 / 抓错 | patterns | `fig_label` / `figure_caption` |
| 章节/课时切分错 | patterns | `chapter` / `lesson` |
| 目录识别错（点线/页码） | patterns + features | `toc_*` 正则 / `features.toc_style` |
| 「解」「答」角色标错 | patterns | `solution_lead` / `pedagogy_patterns` |
| 页眉页脚残留 | features | `running_header_footer` |
| 主题样式 / 排版观感 | assets | `theme_css`（白名单内 CSS 可直接改） |
| ~~大模型改写时的软规则~~ | ~~policy.entries~~ | **本线不生效**；仅 35B 线 `vision_layout` 用得上 |

## 操作路径（成本从低到高）

1. **改规则 YAML**（首选，即时生效、不用发布）：`fs.request_edit` 申请
   `<edition>/layout_rules.yaml` 或 `overrides/pages/NNNN.yaml`，写完
   `corpus.rebuild_ir`（可指定 pages）验证最终 HTML。规则写法见 `rules/40-layout-rules-first.md`。
2. **改几何草稿**（阈值确实量错时）：`editions.save_draft`，参数 `edition_id` + `patch_json`
   （点路径→值的 JSON 字符串），如
   `edition_id="x"`, `patch_json='{"config.geometry.aside_left_x1_max": 128}'`
3. **发布**：`editions.publish` 是闸门。`geometry` / `patterns` / `features` 的草稿
   **不 publish 就不生效**——在验收里不要把未发布的草稿说成已生效。
4. 新教材版式差异大 → `editions.fork` 出新方案，别把通用 `_base` 或别的教材方案改坏

## 怎么验证

- 规则 YAML / page override → `corpus.rebuild_ir` + `harness.page_diff` + 抽样页 `get_page_html`
- geometry 草稿 → 只能在 publish 后验；未发布前如实标注「待发布验证」
- **`harness.run_golden` 不算 MinerU 线的证据**：它只检查几个提示词槽位能否装配，
  而那些槽位在全书导入里不跑。详见 `skills/page-diagnosis.md`「验收」一节。

## 冷启动（新 PDF）

先 `run_calibrate(edition_id="_base", sample_bboxes=[...])` 拿 geometry 候选，
对照现有方案清单（`get_editions_overview`）判断：复用 / fork。
fork 初值 = calibrate 建议的 geometry + 按目测版式设 features + 常用 patterns 从最像的方案抄。

## 禁区

- 别动 `meta.id` / `meta.version` / `meta.base`（系统管理）
- `subject` 必须与 `grade_band` 匹配
- 别把版式规则塞进 `policy.entries` 充数——它不影响导入，属于自欺
- policy entry 的 `facet` 必须在枚举内；`severity` 用 must/should/may
