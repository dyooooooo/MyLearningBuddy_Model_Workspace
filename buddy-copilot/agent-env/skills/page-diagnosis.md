# Skill: 坏页处理与验收（page-diagnosis）

用于 **MinerU 全书导入会话**（`import_advice`）内：escalate 清单优先，或用户聚焦某页后定点修改。

先用 `get_page_context` / `get_page_html` 看清现状，再对照
`doc/mineru-corpus-pipeline-lessons.md` 第 1 节的 A–N 模式表归类，最后选通道。

## 成本阶梯

```text
几何 + 规则  →  高置信页直接过
低置信页    →  job.meta.layout_escalate_pages → Cursor 改块/规则
用户聚焦页  →  同一会话内定点修（patch / replace / rerender）
```

导入主链**不**再调 35B VLM refine。第二级升级目标就是 Cursor。

## 通道 0：导入 escalate 清单（优先）

1. `get_import_job` → 读 `meta.layout_escalate_pages` 与 `meta.layout_escalate_summary`
2. 有清单时：**先处理这些页**，不要满书乱翻
3. 每页对照 `layout_escalate_summary[page].low_keys` / score，再选下面通道

无清单时，再按用户点名的坏页走。

## 修页通道（便宜优先）

| 通道 | 工具 | 适用 | 成本 |
|---|---|---|---|
| ① 改块 / 重渲染 | `patch_block` / `replace_page_blocks` / `rerender_page`，或 page override + `rebuild_ir` | 版式归属错、单块错、渲染层 | 秒级；默认 |
| ② 改教材规则 | `fs.request_edit` 改 `<edition>/layout_rules.yaml` → `rebuild_ir` | 系统性版式问题（多页同症状） | 即时生效，不用发布 |
| ③ 改方案几何 | `editions.save_draft` 改 `config.geometry.*` | 主栏边缘 / 伴生宽度量错 | 需 publish 才生效 |
| ④ 申请改代码 | `fs.request_edit` → 人工 rebuild | 渲染器 / 流水线缺陷 | 最贵；本场景无自动 `docker.rebuild` |

**规则写进 `policy.entries` 不影响导入**（详见 `rules/40-layout-rules-first.md`「规则要生效」一节）。
35B 系列 reprocess mode 已被工具层硬拦截；`reprocess_page` 仅剩 `restore_mineru` / `from_cloud`
两个回退用途，日常修页用上表通道 ①。

## 判断「单页 vs 系统性」

用 `get_import_job` 看总页数与 escalate 清单；抽 2–3 个其他页 `get_page_html` 比对同类症状；
多页同症状 → 通道 ②，并对问题页 + 抽样页验证最终 HTML。

## 验收：改了什么就验什么

每个 Plan 的 `verification` 必须非空，且与改动通道匹配。

| 改了什么 | verification 必须包含 |
|---|---|
| 改块 / 重渲染 | 该页 `get_page_html` 复核 + `harness.page_diff` |
| page override / `layout_rules.yaml` | `corpus.rebuild_ir`（该页或全书 layout-only）+ `harness.page_diff` + 抽样页复核 |
| 方案 `geometry` 草稿 | 需 publish 后才可验；未发布前只能说明「待发布验证」，不得当成已生效 |
| 流水线代码 | `harness.run_pytest`（targets 精确到相关测试文件）+ 问题页验证 |

**`harness.run_golden` 对 MinerU 线不构成证据**：它只检查
`mineru.content_correct` / `mineru.layout_rebuild` / `mineru.unit_judge` / `b35.vision_layout`
四个提示词槽位能否拼装出文本，而前三个槽位在全书导入里一个都不跑。
本线的唯一硬证据是 `rebuild_ir` 之后的最终 HTML。

### pytest targets 速查

| 改动 | targets |
|---|---|
| 声明式规则 / zone 判定 | `tests/test_layout_rules.py`、`tests/test_layout_snapshot.py` |
| column_detect / layout | `tests/test_column_detect.py` |
| document_ir / TOC / 页眉 | `tests/test_document_ir_html.py` |
| mineru_adapter | `tests/test_mineru_adapter.py` |

### 怎么读 page_diff

- `similarity` 接近 1 = 几乎没变（改动可能没生效：查 `rebuild_ir` 返回的写盘页数、
  规则是否落在生效面、该块是否被 `refined_by` pin 住）
- 明显下降 = 有实质变化，让用户在 Studio 预览确认

## 失败处理

- 验收不过：编排器自动回滚白名单文件改动，你会收到失败报告——分析原因、换思路再出 Plan，
  不要原样重试
- 已发布的方案不会自动回滚（发布本身有确认闸门，慎重建议 publish）

## 常见模式速记（详表见 lessons 文档）

E 分栏误判→geometry / layout_rules；H 侧栏叠页→禁 absolute（代码/CSS 层）；
J 编号阶梯→全半角括号+缩进；K CJK 整段变公式→normalize_plain（代码层）；
B 目录当 table→toc 分流（patterns/代码）。
