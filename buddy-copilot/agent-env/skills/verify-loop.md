# Skill: 验收闭环（verify-loop）

每个 Plan 的 `verification` 必须非空，且与改动通道匹配。

## 通道 → 验收组合

| 改了什么 | verification 必须包含 |
|---|---|
| 单页 reprocess | `harness.page_diff`（该页） |
| 方案草稿 | `harness.run_golden`（该方案）+ 问题页 `corpus.reprocess_page` 重跑 + `harness.page_diff` |
| prompt 模板 | 改完文件走 `docker.rebuild`（闸门），随后跑问题页 reprocess + `harness.page_diff` 作为证据 |
| 流水线代码 | `harness.run_pytest`（targets 精确到相关测试文件）+ 问题页验证 |

## pytest targets 速查

| 改动 | targets |
|---|---|
| column_detect / layout | `tests/test_column_detect.py` |
| document_ir / TOC / 页眉 | `tests/test_document_ir_html.py` |
| mineru_adapter | `tests/test_mineru_adapter.py` |

## 怎么读 page_diff

- `similarity` 接近 1 = 几乎没变（改动可能没生效，查 `noop` / `persisted`）
- 明显下降 = 有实质变化，让用户在 Studio 预览确认
- reprocess 返回 `noop: true` → 改动没起作用，重新分析

## 失败处理

- 验收不过：编排器自动回滚白名单文件改动，你会收到失败报告——分析原因、换思路再出 Plan，
  不要原样重试
- 已发布的方案不会自动回滚（发布本身有确认闸门，慎重建议 publish）
