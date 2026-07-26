# Studio 前台平台架构融合方案（v3 · 裁决稿）

> 状态：待实施（本文档仅为方案，不含代码改动）  
> 来源：治理视角评审 × Cursor 性能方案 × Kimi v2 融合稿 × 二次裁决  
> 范围：`exam-ocr-server/studio-ui`、c2-gateway studio 层、前后端契约与实时通道、多前端治理  
> 执行清单：见同目录精神下的 Cursor 执行计划（Step 与下文 Slice 一一对应）

---

## 1. 融合结论与裁决（先看这里）

两份切面重叠约 60%：**性能**（假死）与**治理**（认证/契约/网关/多前端）。冲突经二次对比后拍板如下：

| 冲突点 | 治理/Kimi v2 | 性能/执行稿 | **v3 裁决** |
|--------|--------------|-------------|-------------|
| UI 框架 | 渐进 React 岛（排最后） | Studio 不上 React | **Studio 近半年不做 React 岛**（Slice 4 冻结）；消费级前端立项再 React/Next |
| 状态/数据层 | Zustand + TanStack Query | 自研 selector + 可选 query-core | **采纳 Zustand + TanStack Query**（可用 vanilla 适配，不绑 React） |
| 虚拟列表 | Slice 3 默认上 Virtual | 验收不达标才上 | **先 PageViewportController；Virtual 条件触发** |
| 视口/MathJax | 放 Slice 3（SSE 后） | 放止血同批 | **并进 Slice 1（止血）** |
| 实施顺序 | 安全优先 | 曾偏止血优先 | **Slice 0 安全契约 → Slice 1 止血（含视口）→ Slice 2 SSE → Slice 3 条件 Virtual + N+1 → Slice 5 网关/buddy-client** |

**采纳并保留**

- 假死根因链、Render Plane 概念、可度量 Harness（性能侧）
- P1 认证、OpenAPI 契约、网关瘦身、sanitize、buddy-client、多前端治理（治理侧）
- Redis→SSE、合并双 poll、拆包、懒挂载、MathJax idle（两侧共识）

**明确降级/冻结**

- Studio React 岛：文档保留为远期选项，**本执行周期不排期**
- TanStack Virtual：非默认；Slice 1 验收长任务不达标再开

---

## 2. 定位与诊断

### 2.1 定位

Studio 是平台**长期运营后台**（`doc/plans/corpus-studio.plan.md`；roadmap §7.4 B2B），定义为 **Corpus Ops Shell**。未来家长审核 / 学习看板 / 诊断界面尚无前端归属，是契约资产的下游消费者——**不是**把 Studio 重写成 React 应用的理由。

### 2.2 假死根因链

导入双轮询（2s/2.5s）→ store 全量 emit → Banner/Graph/Note 整页重建 → 同步 MathJax；点章再叠批量 HTML 与面包屑串行 `getNode`。UX 小修不改变该模型。

### 2.3 治理面结构问题

- **P1 认证真空**：`/api/v1/studio/*` 无中间件 + `0.0.0.0:8000` → LAN 匿名 admin（**唯一安全事故级**）
- **P2 契约无源**：OpenAPI 只喂 `frontend/`；studio `types.ts` 手写 + `[key: string]: any`
- **P3 网关藏业务**：上传分派/级联删除/拍照编排长在 gateway；copilot 绕网关直连 buddy-api
- **P4 实时割裂**：Copilot SSE（源为服务端轮询 SQLite）+ 导入 HTTP 轮询；buddy-api 零推送
- **P5 前端无内核**：无 destroy 协议、扁平 store、裸 CustomEvent、`HOST_*` 后门、发散 fetch wrapper

---

## 3. 目标架构：三平面 + 横向治理

| 平面 | 职责 | 目标 |
|------|------|------|
| **Shell** | 布局、Ribbon、窄屏、命令面板、AI 抽屉 | 窄屏 `Ctrl+\`` = 中栏上下分栏（不挤右栏）；`studioBus` 可 dispose |
| **Workspace** | 教材 / 导入(MinerU\|35B) / 搜索 / 待办 / Edition | 按需加载；线生命周期隔离；**保持 Vanilla mount** |
| **Data Plane** | 节点、任务、会话、缓存、订阅 | **TanStack Query + Zustand 切片 + SSE**；UI 只消费投影 |
| **横向治理** | 认证、契约、网关、多前端 | Studio token；OpenAPI 生成类型；网关回归 BFF；`buddy-client` |

设计原则：完全隔离；推优于拉；选择器订阅；视口即真理；重且脆用成熟品；契约下沉；**Studio 不迁框架**。

### 选型（v3）

| 需求 | 结论 | 推荐 |
|------|------|------|
| UI 框架（Studio） | 保持 Vanilla | mount/destroy 协议自研；React 岛冻结 |
| 服务端状态 | 集成 | **TanStack Query**（含 focusManager / 去重） |
| 客户端切片 | 集成 | **Zustand**（vanilla API） |
| 长列表 | 条件集成 | 先视口协议；不达标再 **TanStack Virtual** |
| 公式 | 约束使用 | MathJax + idle/Worker + **可见才 typeset** |
| 任务进度 | SSE | Redis→buddy-api/gateway 扇出；Query `setQueryData` |
| 事件总线 | 自研薄 | `studioBus` + 事件注册表（常量 + payload） |
| API 客户端 | 自研+生成 | `http.ts` + openapi 生成类型 |
| 图/编辑器 | 按需 | vis-network、tiptap 动态 import，关闭即 destroy |

---

## 4. 实施切片（按依赖；每片可独立验收）

### Slice 0 — 安全与契约基座（1–2 天）

- [ ] 网关 studio 认证中间件（Studio token，浏览器注入）→ 无 token 401
- [ ] 统一 `http.ts`：`ApiError`、超时、AbortController；收敛 5 份 wrapper
- [ ] `gen-api-types.sh` 指向 studio-ui；删 `[key: string]: any`
- [ ] sanitize 收口（B35/Corpus feedback、`renderMarkdown`）；409 / 401 UI

验收：LAN 无 token 打 proxy → 401；`tsc --noEmit`；types 为生成物。

### Slice 1 — 止血（纯前端为主，2–4 天）— **含视口/MathJax**

- [ ] 引入 TanStack Query + Zustand；双 poll → 单一 `importJob` 观察者；visibility 门控
- [ ] Banner / 进度 / 当前线面板条件订阅；NoteEditor / Graph 不被导入 tick 重建
- [ ] TERMINAL 含 `chunked/review/published`；404/连错停轮询
- [ ] **`PageViewportController`**：页壳 + 视口±N；可见才 MathJax；idle 分片；poll tick 禁全书 typeset
- [ ] 导入预览与 `TextbookScrollReader` 共用视口协议
- [ ] 按线动态加载 MinerU/B35；`manualChunks`（mathjax / vis / 各工作区）；主 chunk &lt; 1.2MB
- [ ] Graph / TipTap：按需加载 + 真 destroy；删 `pipelineStatusPoll.ts`
- [ ] `studioBus` + 事件注册表；Copilot `HOST_*` → 显式服务
- [ ] Shell 契约：窄屏 AI 上下分栏（文档/注释收口）

验收：隐藏页无轮询；终态无 poll；编辑不被打断；主 chunk &lt; 1.2MB；100+ 页预览 30s 长任务基线。  
**门禁**：若长任务仍超标 → 开启 Slice 3 Virtual；否则跳过 Virtual。

### Slice 2 — 推送通道与字段接电（约 1 周）

- [ ] worker → Redis channel；buddy-api/gateway SSE：`job.progress` / `page.ready` / `job.terminal`
- [ ] 前端单 EventSource + Query `setQueryData`；轮询仅兜底
- [ ] Copilot 与导入事件同一客户端路由（可分 `line` 字段，禁止串线）
- [ ] `missing_pages`、`chunk_progress`、extend + 409

验收：进度经 SSE；缺页确定态；acceptance「终态无 poll」。

### Slice 3 — API 减负 + 条件 Virtual（按门禁）

- [ ] 面包屑/章树批量或嵌入式 API；页 HTML 增量或跟 `page.ready`
- [ ] **仅当 Slice 1 门禁失败**：TanStack Virtual（页卡/页码条/树）与 StickyPageStrip 共用页索引

验收：N+1 消失；若启用 Virtual，则预览长任务达标。

### Slice 4 — React 岛（冻结）

- [ ] ~~新面板 React 岛~~ — **本周期不实施**；待消费级前端立项或 Studio 单面板重写 ROI 明确后再开
- [ ] 消费级前端（M5/M7）立项即 React/Next，复用 `buddy-client` / Data Plane 契约

### Slice 5 — 网关瘦身与多前端治理（Slice 2 后持续）

- [ ] 上传分派/级联删除下沉或显式端点；copilot→buddy-api 审计统一
- [ ] `packages/buddy-client`：生成类型 + typed client + ApiError + SSE
- [ ] `/test/` → Android 迁移后降级为验收 harness（文档裁决）

---

## 5. 验收基线（Harness）

- Performance：「100+ 页导入预览 30s」— 长任务 &lt;50ms 占比、`importJob` 频率、首包体积
- acceptance：终态无 poll；隐藏页无轮询；SSE 断线回退轮询且恢复去重
- 安全：无 token 全 401；sanitize 覆盖双导入线 + markdown 出口

---

## 6. 明确不做

- 不整仓 React 重写；**本周期不做 Studio React 岛**
- 不默认上 Virtual（门禁触发除外）
- 不自研虚拟列表/请求缓存引擎（用 Query/Zustand；Virtual 按需）
- 不上 WebSocket；不动 DeepTutor；不把 ③ 写入 exam-ocr/Studio

---

## 7. 关键文件

- 前台：`state/store.ts`、`services/importProgress.ts`、`ImportPipelinePanel.ts`、MinerU/B35 面板、`math-render.ts`、`TextbookScrollReader.ts`、`vite.config.ts`、`LayoutShell.ts`、`AiTerminal.ts`、`CopilotPanel.ts`
- 后台：`exam-ocr-server/app/main.py`、`studio/routes.py`、buddy-api worker（Redis 事件）、`tools/gen-api-types.sh`

---

## 8. 版本说明

| 版本 | 含义 |
|------|------|
| v1 | 治理向 Phase 方案（偏自研内核、不做 Virtual） |
| v2 | Kimi 融合（全面成熟库 + React 岛排后 + Virtual 默认） |
| **v3** | 二次裁决：Query/Zustand 跟 v2；视口进止血；React 岛冻结；Virtual 条件触发 |
