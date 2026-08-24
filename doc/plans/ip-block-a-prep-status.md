# Block A — 整理状态（prep_status）+ 拆分 / 矛盾

> 日期：2026-08-04  
> 状态：**已实现**；其后纠正：入库一律 `needs_triage`；「整理 / 拆分提案」= Cursor SDK Agent via `buddy-copilot`（非 buddy-api vLLM）；`needs_aggregate`（需聚合，兼容旧码 `needs_conflict_resolve`）  
> 总计划：[ip-four-step-chunked-exec.md](ip-four-step-chunked-exec.md)  
> 权威设计：[improvement-point-management.md](../improvement-point-management.md) §11  

## 1. 目标与边界

| 做 | 不做 |
|----|------|
| 列 `prep_status`；与 `needs_split` 对齐 | 抽象三层 UI（Block B） |
| 拆分提案 + 用户确认硬拆 | IP 向量 / Step4（Block C/D） |
| 矛盾对检测 + 人裁决留/删 | 导入四步 / L0（E–G） |
| Studio 整理状态筛选与工作台入口 | 改 `status=accepted` 语义 |

**独立价值**：台账可卫生；非 `ready` 为后续抽象闸门。不改导入主链。

## 2. 数据模型

表：`corpus_improvement_points`（[`learning-buddy-api/app/db/models.py`](../../learning-buddy-api/app/db/models.py)）

| 字段 | 类型 | 说明 |
|------|------|------|
| `prep_status` | `str`（新增） | 闭集：`needs_split` \| `needs_conflict_resolve` \| `ready` |
| `needs_split` | `int`（已有） | 0/1；与 `prep_status==needs_split` **双写一致** |

迁移：[`migrate.py`](../../learning-buddy-api/app/db/migrate.py)

```text
ALTER TABLE corpus_improvement_points
  ADD COLUMN IF NOT EXISTS prep_status VARCHAR(32) DEFAULT 'ready';
-- 可选：INDEX (fingerprint_hash, prep_status)
```

界面文案（整理状态标签）：

| prep_status | 中文 |
|-------------|------|
| `needs_split` | 待拆分 |
| `needs_conflict_resolve` | 待裁决矛盾 |
| `ready` | 完成体 |

**与兼容字段 `status=accepted` 无关**：进表即有效；整理状态只服务预抽象流水线。

## 3. confirm 写入规则

现有 [`guess_needs_split`](../../learning-buddy-api/app/services/improvement_points.py) / `confirm_improvement`：

```text
if guess_needs_split(summary) or 请求显式 needs_split:
    prep_status = needs_split
    needs_split = 1
else:
    prep_status = ready
    needs_split = 0

# 同事务轻量矛盾检测（可选但本块应做）：
# 若与同书其它 ready|needs_conflict_resolve 对立
#   → 双方 prep_status = needs_conflict_resolve
```

## 4. 存量回填

migrate 后或 admin 一次性脚本：

| 条件 | prep_status |
|------|-------------|
| `needs_split = 1` | `needs_split` |
| 否则 | `ready` |

本块**不做**全库语义矛盾扫描进 `needs_conflict_resolve`（避免误伤）。矛盾靠按书触发的检测 API。

## 5. API（admin，经 Studio `/proxy/improvement-points/*`）

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `improvement-points?prep_status=` | 列表过滤（扩展现有 list） |
| POST | `improvement-points/{id}/split-propose` | 启发式/规则提案：`[{summary, category, procedure_slice?}]`，**不写库** |
| POST | `improvement-points/{id}/split-apply` | 人确认后：创建 N 条新 IP；**旧条 purge**；新条继承 fingerprint / job / origin；procedure 按切片或复制后标注 |
| GET | `improvement-points/conflict-pairs?fingerprint_hash=` | 冲突对列表 |
| POST | `improvement-points/conflict-resolve` | body：`keep_ids[]` / `delete_ids[]`；删除走 `purge_improvement_point`；保留者若无对立 → `ready` |

**保留不动**：现有 `POST .../resolve-conflict`（lifecycle `status=conflict`）。整理矛盾走新端点，避免语义搅在一起。

`_ip_out` 增加：

- `prep_status`
- `prep_status_label`（待拆分 / 待裁决矛盾 / 完成体）

**预留闸（本块加服务端检查）**：挂 pattern / 抽象相关 API 若 `prep_status != ready` → **403**（Block B 复用）。

### 5.1 split-apply 细节（已锁定）

1. 校验源 IP 存在且 `prep_status=needs_split`（或 `needs_split=1`）。  
2. 对每条提案创建新 IP：`status=accepted`，`origin`/fp/job 继承，`summary`/`category`/procedure 来自提案。  
3. 对新条跑矛盾检测，可能变为 `needs_conflict_resolve`，否则 `ready`。  
4. `purge_improvement_point(旧 id)`（含 corrections/links 等既有清理）。  
5. 返回新 id 列表。

### 5.2 conflict-resolve 细节（已锁定）

1. `delete_ids` 逐个 `purge_improvement_point`。  
2. `keep_ids`：清除与已删方的对立后，若无剩余对立对 → `prep_status=ready`，`needs_split=0`。  
3. 幂等：已删 id 再删不报致命错（或明确 404 策略，实现时统一为「跳过已不存在」）。

## 6. 矛盾启发式（本块够用即可）

同 `fingerprint_hash` 下，两两处于 `ready|needs_conflict_resolve`：

- 同页 + 摘要高度相似但动词对立（删/留、取消/恢复、隐藏/显示等词对），或  
- 已有 `corpus_improvement_links.rel=conflicts`  

命中则双方 `prep_status=needs_conflict_resolve`。不追求完美 NLP。

## 7. Studio

| 文件 | 改动 |
|------|------|
| [`exam-ocr-server/studio-ui/src/api/improvementPoints.ts`](../../exam-ocr-server/studio-ui/src/api/improvementPoints.ts) | 类型 + 上述 API |
| [`ImprovementsPanel.ts`](../../exam-ocr-server/studio-ui/src/views/ImprovementsPanel.ts) | 整理状态筛选芯片；行上展示整理状态 |
| [`ImprovementsInspector.ts`](../../exam-ocr-server/studio-ui/src/views/ImprovementsInspector.ts) | 拆分工作台 / 矛盾裁决；速查说明与旧「有效/旧版」不是同一列 |

部署重建（代码进镜像）：

```bash
cd learning-buddy-c2
```

```bash
docker compose build buddy-api buddy-worker c2-gateway
```

```bash
docker compose up -d buddy-api buddy-worker c2-gateway
```

## 8. 测试

[`learning-buddy-api/tests/test_improvement_points.py`](../../learning-buddy-api/tests/test_improvement_points.py)：

1. confirm 复合摘要 → `prep_status=needs_split`  
2. split-apply 后旧 id 不可用、新条存在且多为 `ready`（或无矛盾）  
3. conflict-resolve：删除方 purge、保留方 `ready`  
4. 非 ready 时挂 pattern → 403  

## 9. 验收清单

1. DB 有 `prep_status`；存量已回填  
2. 列表可按整理状态筛  
3. 拆分 / 矛盾人在环走通  
4. `origin` / `status=accepted` 语义不变  
5. 不碰 import35b 线  
6. 写码轮完成后：权威文 §11 补一句「Block A 已实现」  

## 10. 开码顺序建议

1. migrate + model + `_ip_out` + 回填  
2. confirm 双写 + list 过滤  
3. split-propose / split-apply + 单测  
4. conflict-pairs / conflict-resolve + 单测  
5. pattern 挂载 403 闸  
6. Studio 筛选 + Inspector 工作台  
7. 重建镜像 + 手工验收  

## 11. 下一轮

Block A 已落地。下一块按总计划推进（通常 Block C 向量或 Block B 抽象 UI）。  
