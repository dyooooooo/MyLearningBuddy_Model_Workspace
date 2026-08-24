# 本书改进点（ip_book）

**一书指纹 ↔ 一条 AI 对话**。整理 / 全部拆分 / 拆分本条 / 处理全部聚合（或单簇）都在本会话完成，禁止为同一 `fingerprint_hash` 再开平行会话。

按 payload.`intent` 执行当前任务（`triage` | `split_all` | `split_one` | `aggregate`）。写库后复拉对应队列，直到归零再交最终报告。不要直调 vLLM。

## 意图：triage（整理）

清空该书全部 `needs_triage`。

1. `ip.list_for_triage(limit=2000)` — 看 `total_remaining` / `has_more`
2. 可选 `ip.conflict_hints`
3. 本批每条 id 必须进入 `ready_ids` / `split_ids` / 某 `aggregate_groups.member_ids` → `ip.triage_apply`
4. 再 list，直到 `total_remaining=0`
5. 报告：`ready_count` / `split_count` / `aggregate_count` / **`remaining_needs_triage=0`**

- 同条既像拆又像对立 → 先 `split_ids`
- 禁止故意漏网；过简则能拆进 split，否则 ready

## 意图：split_all（全部拆分）

清空该书全部 `needs_split`。点「全部拆分」= 已授权逐条 `split_apply`。

1. `ip.list_for_split(limit=2000)`
2. 对每条：`split_propose(children)` → 校验通过后 `split_apply`
3. 再 list，直到 `total_remaining=0`
4. 报告：`split_done_count` / **`remaining_needs_split=0`** / 失败原因

硬规则：按成功实施路径切；禁止整包复制父 procedure；after 互不重叠且分完。

## 意图：split_one（拆分本条）

payload 含 `ip_id`。向用户展示提案，**确认后再** `split_apply`。

1. `ip.split_propose(ip_id)` 探测
2. `ip.split_propose(ip_id, children)` 组装校验
3. 展示子条；确认后 `split_apply`
4. 报告含 `new_ids`、errors、summary

## 意图：aggregate（处理聚合）

- **全部**：`process_all=true`（右栏「处理全部聚合」）→ 按簇#1、簇#2…处理全书
- **单簇**：`process_all=false` + `aggregate_cluster_label`（如 `簇#1`）+ `member_ids`（中栏点簇标题）→ **只裁决该簇**
  - **禁止**问「全部还是一个」；范围已在用户消息里写死
  - **禁止**改其它簇的 id

1. 单簇：直接对本簇 member_ids 成对说明；全书：先列簇再逐簇说明
2. **用户确认后** `ip.aggregate_resolve`
3. 勿静默删除；报告含 cluster_label / kept / deleted / summary

## 共用硬规则

- 新子条进待整理，需再跑整理做矛盾检测
- tool 必须来自父 steps；每子条完备（真实 tool + 非空 args）
- 共享准备步骤可复制进多个子条的 step_indices；改动实质步骤优先不重复归属
