# 页重处理（reprocess）地图

入口：`POST /api/v1/admin/corpus/import/jobs/{job_id}/pages/{page_no}/reprocess`
body：`{"mode": "...", "user_note": "可选提示，会注入 prompt"}`
实现：`learning-buddy-api/app/services/corpus_reprocess_service.py`

## mode 语义（选对通道的关键）

| mode | 行为 | 何时用 |
|---|---|---|
| `content_correct` | 只改文字/LaTeX，版式拓扑冻结 | 文字错、公式错，版式没问题 |
| `layout_content` | 文字+分区+角色一起修（refine mode=rerecognize）；`refine_35b` 是别名 | 版式和内容都有问题（默认） |
| `layout_rebuild` | 重建骨架 + 对齐 + 裁决 + 融合 | 页面结构整体崩了 |
| `restore_mineru` | 回滚到 MinerU 基线；`mineru_rerecognize` 是别名 | 越修越糟，回退重来 |
| `rerender` / `from_blocks` | 仅存档 IR 重渲 HTML（无 MinerU、无 35B） | 只是渲染/CSS 层问题 |
| `from_cloud` | 从 MinerU content_list 重新适配 | adapter 修复后回放 |

响应关键字段：`ok, mode, persisted, figure_count, finding_count, findings,
refine_patches, effective_changes, noop, judge_summary, used_35b, used_mineru`。

## 单块级

`POST .../blocks/{block_id}/refine`，body `{user_note}` —— 只动一个块。

## 页上下文

`GET .../pages/{page_no}/rerecognize-context?block_id=`：
`ir_blocks[]`（IR 块：id/type/page_no/order/bbox/text/…）、`profile`、`parse_engine`、
`fingerprint_preview`、`stages`（debug 工件索引）、`refine_payload`。

## 场景隔离

| 场景 | 可用 mode | 排版重写 |
|------|-----------|----------|
| `import_advice`（MinerU AI 分段） | 仅 `rerender` / `restore_mineru` / `from_cloud` | **禁止** 35B refine；用 `corpus.replace_page_blocks` / `patch_block` |
| `page_assist`（页级分诊） | 上表全套（含 35B） | 可 `layout_content` / `layout_rebuild` |

## 分诊决策树（场景 B：page_assist）

1. 只有这一页坏、别的页好 → 单页 reprocess（选对 mode）
2. 同类症状多页出现（分栏/侧栏/目录系统性错）→ 改方案草稿 `editions.save_draft`
   （geometry/patterns/policy），再对该页+抽样页 reprocess 验证
3. prompt 指令本身有缺陷（模型总是犯同一类语义错）→ 改 `data/prompts/*.md`（昂贵，rebuild）
4. 代码逻辑 bug（adapter/column_detect/render）→ 改代码 + pytest（昂贵，rebuild）

坏页现象分类表（A–N 模式）见 `doc/mineru-corpus-pipeline-lessons.md` 第 1 节。
