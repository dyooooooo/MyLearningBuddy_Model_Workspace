# 改进点一次性迁移记录（迁移场 · Cursor IDE）

> **历史快照（2026-07-30）**。当时曾写 `pattern_status=draft`、多来源码；  
> 2026-08 清理后：来源闭集 `ai_auto`/`user_ask`，默认不进抽象轨，成员已解绑旧模式。  
> 现行权威：`doc/improvement-point-management.md`。

> **场所**：Cursor IDE Coding Agent 本对话（admin HTTP）  
> **非**：Studio Buddy Copilot / SDK Agent  
> **日期**：2026-07-30

## 做法

1. 从 `corpus_corrections` 取出 `user_note` 非空且无 `ip_id` 的行。  
2. 同指纹同页保留最新一条（同类合并）。  
3. 调 `POST .../improvement-points/confirm`（带 procedure）；`write_correction=false`。  
4. `UPDATE` 原 correction 挂上 `ip_id` + `procedure_json`（保留 after_json 供回放）。  
5. 第二册指纹对齐 job `821d05bc…`；`bind-edition` → `hujiao_math_hs_v1_ai_20260725`；`converge`。  
6. 可跨书主题标 `pattern_status=draft`（math/title/layout/figure/exercise/table）。

## 结果

| 项 | 值 |
|----|-----|
| 有 note 的旧 correction | 77 |
| 合并后 confirm | **61**（失败 0） |
| 第二册 fp `038e8a19…` | 页 4/8/11/12 → accepted；平台按 job/fp 可查 |
| 专书绑定 | 已绑定 |
| 无 note 的旧行 | 未晋升；回放改完后忽略（无 accepted `ip_id`） |
| smoke-job IP | 仍在库，UI 按书筛选即可 |

## 明确未做

- 未建迁移模块 / migrate API  
- 未把 Markdown 报告整页粘成 IP  
- 未自动落地 L0/L1（仅 draft 模式草案）

---

## 第二轮补录（同日 · 统一页确认主链之后）

> **原因**：第一轮只吃到 `corpus_corrections` 有 note 的 4 条第二册行；真实劳动在会话 `effective_changes`（157）与 `layout_change_log`（125）。  
> **场所**：仍为 Cursor IDE × admin HTTP（非 Studio 迁移 UI）。

### 做法

1. 从 `buddy-copilot/data/copilot.db` 会话 `e76d566110884eae` 读取 `effective_changes`。  
2. 按 `(page, kind, summary[:80])` 去重 → **138** 条。  
3. 每条 `POST .../improvement-points/confirm`：挂同页 `layout_change_log` 作 attempts；`write_correction=true`；`pattern_status=draft`（edition）。  
4. `converge`；bind 仍为 `hujiao_math_hs_v1_ai_20260725`。

### 结果

| 项 | 值 |
|----|-----|
| confirm 成功 | **138**（失败 0） |
| 指纹 accepted（含前轮） | **197** |
| converge | ok，conflicts=0 |
| 专书绑定 | 未变 |

### 同步代码修复（日常场）

Studio「确认有效改进」按钮：先 `improvement-points/confirm`，再 `corrections/enqueue(source_ref=ip_id)`，与 `edition.record_effective_change` 同出口。
