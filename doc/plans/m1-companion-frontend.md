# M1 实施计划 — 伴学前台先行（/test/ + 后台）

> **策略变更（2026-06）**：M1 剩余交付**不在 Android 上先做**，而在 Spark **c2-gateway `/test/` 伴学测试台** + **learning-buddy-api** 上完成；验收通过后，再以同一套 `/api/v1/companion/*` 与 Buddy API 协议迁移到 MyLearningBuddy 平板。

## 为什么前台先行

| 原路径 | 新路径 |
|--------|--------|
| 平板 Kotlin 实现 Phase C / 错题 / A⁺ UI | 浏览器 `/test/` 实现同等能力 |
| Android ↔ gateway 联调 | 前台 JS ↔ gateway ↔ buddy-api 联调 |
| 双端各写一套 UI 规则 | **网关 + Buddy API 为唯一业务真相**；Android 仅换壳 |

```text
浏览器 /test/  ──⑥ 模拟──►  c2-gateway :8000  ──C2/B 编排──►  vLLM
                              │
                              └──ingest──►  learning-buddy-api :8100  ──►  A⁺ / LangGraph
```

Android（MyLearningBuddy）在 M1 **不阻塞** Spark 验收；M1 完成后再做薄客户端对接。

---

## M1 完成标准（不变）

一次伴学会话结束 → **A** 有题目实体（`question_entities`）→ **A⁺** 有对应知识点更新 → 仍可无完整 ①③ 产品化。

**M1 验收入口**：`http://192.168.3.200:8000/test/` + `learning-buddy-c2/scripts/acceptance-pilot.sh`

---

## 用户选择（2026-06）

| 项 | 选择 |
|----|------|
| Phase C 连续相机 | **跳过**（Android/后续） |
| 切题 bbox | **完整**（末帧 + 每题切帧） |
| 题目卡片 | **完整**（dwell/eval/RAG 可展开） |
| A⁺ 视图 | **完整**（fluency + tier） |
| 合并 Tab | **统一 + 定稿前预标错题** |
| Harness | **acceptance-companion-m1.sh → pilot** |
| 学生注册 | **半自动 ensure-student** |

---

## 交付清单

### 已有（M0/M2/M3 骨架，可复用）

| 项 | 层 | 位置 | 状态 |
|----|-----|------|------|
| 伴学会话 + 加帧 + 合并 | C2/B | `exam-ocr-server/app/companion/` | ✅ |
| 定稿 → Buddy ingest | A | `buddy_client.ingest_homework` | ✅ |
| 作业库 / 按题展示 | A / ⑤ | `/test/` Tab「作业库」 | ✅ |
| 错题标记 + 错题本 | B / ⑤ | PATCH + Tab「错题本」 | ✅ |
| A⁺ 掌握列表 | A⁺ | Tab「A⁺」← Buddy mastery | ✅ |
| Agent 运行观测 | ③ | Tab「Agent」← graph-runs | ✅ |
| 帧级 dwell 估算 | ⑥/B | `question_timing.py` → ingest meta | ✅ 后台 |
| LangGraph + eval/aplus | ③/A⁺ | buddy-worker | ✅ |

### M1 待做（全部前台 + 后台）

| # | 交付 | 层 | 前台 | 后台 | 优先级 |
|---|------|-----|------|------|--------|
| 1 | **Phase C 连续采帧** | ⑥ | — | — | **跳过（用户选择）** |
| 2 | **切题实体可视化** | C2→A | 作业详情：印刷/作答 **bbox 叠图**；合并/本帧 Tab 已有 overlay | OCR schema 已有 bbox | **P0** |
| 3 | **题目卡片增强** | A/B | 展示 `dwell_ms`、`eval_confidence`、RAG 证据摘要 | Buddy `QuestionOut` 已有字段 | **P1** |
| 4 | **A⁺ 能力视图增强** | A⁺ | mastery 卡片展示 `fluency` / `fluency_ratio` / `wrong_rate` | Buddy `MasteryView` 已有 | **P1** |
| 5 | **定稿前预览按题** | ⑥/C2 | 「合并」Tab 按题卡片与定稿后样式一致（含 timing） | — | **P1** |
| 6 | **M1 前台验收脚本** | Harness | `acceptance-companion-m1.sh`：Phase C 模拟 / 错题 / mastery 字段 | 扩 pilot 或独立 | **P1** |
| 7 | **学生注册引导** | 运维 | 前台检测 Buddy 无学生时提示调 Admin API | 已有 admin students | **P2** |

### 明确不在 M1（Android / 后续）

- MyLearningBuddy Room 同步、Kotlin 相机 Phase C
- 讲解 / 推题 / 家长审核（M5）
- Memory Drive 自动触发（M4）

---

## 实施顺序建议

```text
Step 1  文档与 harness 对齐（本文件 + roadmap §M1）
Step 2  前台：mastery / 题目卡片字段补齐（小改 render-utils.js）
Step 3  前台：作业详情 bbox 叠图（复用 overlay 逻辑 + 定稿帧图）
Step 4  前台：Phase C 连续相机模块（companion-ui.js + index.html）
Step 5  acceptance-companion-m1.sh + 纳入 acceptance-pilot.sh
Step 6  Android 对接说明（仅文档：X-Student-Id、API 列表）
```

---

## 关键 API（Android 将来复用同一套）

| 用途 | 方法 | 路径 |
|------|------|------|
| 加帧 | POST | `/api/v1/companion/sessions/{id}/frames` |
| 定稿 | POST | `/api/v1/companion/sessions/{id}/finalize-page` |
| 作业列表 | GET | `/api/v1/companion/homework` |
| 标错题 | PATCH | `/api/v1/companion/questions/{id}` |
| A⁺ | GET | `/api/v1/companion/buddy/mastery` |
| Agent | GET | `/api/v1/companion/graph-runs` |

请求头：`X-Student-Id: student-01`（须先在 Buddy Admin 注册）。

---

## 验收场景（M1 前台）

| # | 场景 | 预期 |
|---|------|------|
| 1 | 连续相机 30s 内自动加帧 ≥2 | Timeline 有 merge；合并结果手写递增 |
| 2 | 定稿 → 作业库 | Buddy 有 `question_entities`；Agent run succeeded |
| 3 | 作业详情 | 按题展示 print/handwriting；可选 bbox |
| 4 | 标错题 → 错题本 | `is_wrong=true`；错题本按 kp 分组 |
| 5 | A⁺ Tab | 至少 1 kp；展示 fluency / wrong_rate（有 dwell+错题时） |
| 6 | 全栈 | `acceptance-pilot.sh` PASS |

---

## 相关文档

- 伴学 Pipeline 网关计划：[伴学_pipeline_前台_05e58a7a.plan.md](伴学_pipeline_前台_05e58a7a.plan.md)（M0 已完成）
- Roadmap：[learning-buddy-status-and-roadmap.md](../learning-buddy-status-and-roadmap.md) §M1
- 部署：[learning-buddy-c2/README.md](../../learning-buddy-c2/README.md)
