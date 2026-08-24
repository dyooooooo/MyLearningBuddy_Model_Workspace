# 完备路径拆分（ip_split）

对 `prep_status=needs_split` 的改进点，按 **成功实施路径** 切开，不是只切摘要句子。

有两套入口（互不混用）：

| 入口 | payload | 行为 |
|------|---------|------|
| **拆分本条** | 有 `ip_id` | 只处理这一条；向用户展示提案，确认后再 `split_apply` |
| **全部拆分** | `process_all=true` + `fingerprint_hash` | 清空该书全部待拆分；点按钮即授权写库 |

## 工具

1. `ip.list_for_split` — **全部拆分**时先拉队列（看 `total_remaining` / `has_more`）
2. `ip.split_propose(ip_id)` — 探测：可能 `needs_agent` + 返回 `steps` / `after_index`
3. 规划子条后 `ip.split_propose(ip_id, children=[...])` 组装校验：

```json
{
  "summary": "单主题摘要",
  "category": "layout",
  "step_indices": [0, 2],
  "after_indices": [1]
}
```

4. `ip.split_apply` — **禁止**把父 procedure 整包复制给每个子条
5. `tool` 必须来自父 `steps`；每子条须完备（真实 tool + 非空 args）
6. 父 after 块索引 **互不重叠且全部分完**；有 `unassigned_after_indices` 则不可 apply

## 全部拆分流程

1. `ip.list_for_split(limit=2000)`
2. 对列表中 **每一条** id：propose(children) → 校验通过后 `split_apply`
3. 再 list；`total_remaining>0` 则继续，直到为 0
4. 最终报告：`split_done_count`、`new_ids` 摘要、**`remaining_needs_split=0`**、失败项原因

点「全部拆分」视为用户已授权对本批逐条 `split_apply`，无需再逐条弹工作台确认。

## 拆分本条流程

1. 对给定 `ip_id` 探测并组装提案
2. **向用户展示**子条摘要；用户确认后再 `split_apply`
3. 报告含 `new_ids`、errors、summary

## 硬规则

- 共享准备步骤可复制进多个子条的 step_indices
- 改动实质步骤优先不重复归属
- 新子条进待整理，需再跑整理做矛盾检测
