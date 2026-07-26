# Skill: 坏页分诊（page-diagnosis）

先用 `get_page_context` / `get_page_html` 看清现状，再对照
`doc/mineru-corpus-pipeline-lessons.md` 第 1 节的 A–N 模式表归类，最后选通道。

## 四条通道（成本递增）

| 通道 | 工具 | 适用 | 成本 |
|---|---|---|---|
| ① 单页重处理 | `corpus.reprocess_page` | 只有该页坏 | 秒级，无副作用 |
| ② 改方案草稿 | `editions.save_draft` | 系统性版式问题（多页同症状） | 即时生效，需重跑问题页验证 |
| ③ 改 prompt 模板 | `fs.request_edit` 获批后改文件 + `docker.rebuild` | 模型总犯同一类语义错 | 昂贵：rebuild 约 2–5 分钟 |
| ④ 改流水线代码 | `fs.request_edit` 获批后改文件 + `harness.run_pytest` + `docker.rebuild` | adapter/分栏/渲染逻辑 bug | 最贵，最后手段 |

## 通道 ① 的 mode 选择

- 文字/公式错，版式对 → `content_correct`
- 版式+内容都有问题 → `layout_content`（默认）
- 整页结构崩 → `layout_rebuild`
- 越修越糟 → `restore_mineru` 回基线重来
- 只是渲染层（CSS/HTML）问题 → `rerender`
- 把用户原话精炼成 `user_note` 传入（会注入 prompt）

## 判断「单页 vs 系统性」

用 `get_import_job` 看总页数，抽 2–3 个其他页 `get_page_html` 比对同类症状；
多页同症状 → 通道 ②，并在 Plan 的 verification 里对问题页+抽样页都跑 `harness.page_diff`。

## 常见模式速记（详表见 lessons 文档）

E 分栏误判→geometry；H 侧栏叠页→禁 absolute（代码/CSS 层）；J 编号阶梯→全半角括号+缩进；
K CJK 整段变公式→normalize_plain（代码层）；B 目录当 table→toc 分流（patterns/代码）。
