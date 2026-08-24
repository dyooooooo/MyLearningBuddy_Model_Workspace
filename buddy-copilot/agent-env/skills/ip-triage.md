# 改进点整理（ip_triage）

进表改进点初始均为 **待整理**。用户点「整理」= **清空该书全部待整理**，经工具写库；不要直调 vLLM。

## 工具顺序（可多轮，直到待整理为 0）

1. `ip.list_for_triage`（`limit=2000`）— 看 `total_remaining` / `has_more` / `items`
2. `ip.conflict_hints` — 可选，看规则对立提示（不写库）
3. 对本批 **每一条** id 分类后调用 `ip.triage_apply` 写库
4. **再** `ip.list_for_triage`；若 `total_remaining > 0` 或 `has_more`，重复 3–4
5. 结束前确认 `total_remaining == 0`，再写最终报告

```json
{
  "ready_ids": ["…"],
  "split_ids": ["…"],
  "aggregate_groups": [
    { "member_ids": ["a", "b"], "reason": "同页删/留对立" }
  ]
}
```

## 待拆分（split_ids）

一条记录捆了多个可独立成立的改进；或 steps 可分成 ≥2 组且每组单独完备；或跨页/多无关 block。  
**不拆**：单主题多步因果链；与别条对立 → 进聚合。

## 需聚合（aggregate_groups）

同作用对象 + 处置不可并存（删/留、隐/显）。近义重复不要进组（留给 converge）。每组 `member_ids` ≥ 2。

## 完成体（ready_ids）

单主题、路径完备、无未决对立。

## 硬规则

- **禁止故意漏网**：本批 `items` 里每条 id 必须进入 `ready_ids` / `split_ids` / 某个 `aggregate_groups.member_ids` 之一
- 说明过简或犹豫时：能拆则 `split_ids`，否则标 `ready_ids`（勿留待整理）
- 同条若既像拆又像对立 → **先放 split_ids**
- 不要把未看过的 id 标 ready
- 最终报告：`ready_count` / `split_count` / `aggregate_count` / **`remaining_needs_triage`（必须为 0）** / summary
