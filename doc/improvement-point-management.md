# 改进点（IP）管理 — 设计与字段字典

> **权威设计（以本文为准）**。页级有效改进以 IP 为唯一真相源；L3 `corpus_corrections` 仅由有效 IP 派生。  
> 更新：2026-08-04 — `origin` / `summary` / `kind` / `needs_split`；来源闭集两值；默认不进抽象轨；硬删 API。  
> 目标蓝图（设计态，未全量实现）：**整理状态 `prep_status`**、四步导入、经验库三级、L0 双提案/末段确认、Step4 检索优先。详见 §11–§14 与 Canvas `ip-rag-four-step-blueprint`。

## 0. 用语约定（迁移场 vs 日常场）

| 名称 | 指什么 | 干什么 |
|------|--------|--------|
| **迁移场** | Cursor IDE × Coding Agent 对话 | **仅一次**：盘点旧资产 → 调 buddy-api **admin HTTP** 写入 IP（历史债） |
| **日常场** | Studio 内嵌 Buddy Copilot（Cursor SDK Agent） | 导入 → 改页 → `record_effective_change` → 改进点台账 → 同书回放 / 可选抽象 |

- 一次性历史债**不**在 Studio Copilot 里做。  
- **不**做系统内迁移模块 / migrate API / 迁移面板。

## 1. 目标

1. **专书可恢复**：指纹 + L3 页补丁（非空 `after`）+ procedure → 同书再导自动回放。  
2. **类似书可复用（可选）**：仅 `prep_status=ready`（完成体）可抽象成手册后，再定落地目标并写盘。  
3. **过程可复现**：每条必须有**成功实现路径**；没有的禁止进表。  
4. **经验可检索（目标）**：完成体 IP 进向量库；对话改页先检索再自创。

## 2. 日常主链（现行）

```text
Studio Copilot 会话绑定 job + fingerprint
  → MinerU 全书 start_import → 页 IR
  → 逐页优化（试错只留过程日志，不进改进点）
  → 某改动事项最终成功 → edition.record_effective_change / confirm
       → 写入改进点（进表即有效；origin = ai_auto | user_ask）
       → 派生 corpus_corrections（ip_id；after 应为成功后的块快照）
       → pattern_status 默认 none
  → 同书再导入：apply_matched_corrections 回放非空 after 的 L3
  → （可选）整理至 ready → 抽象 → 才定 landing_target 并写盘
```

硬门闩：页面已写盘成功却漏写改进点 → 收尾应告警。  
禁止：只写 `EditionCorrectionQueue` 而不调 confirm；流水线/探针事件进改进点表。

**产品列说明**：已去掉旧的「有效/旧版/应用层」生命周期列。目标态新增的是 **整理状态（prep_status）**（预抽象流水线，见 §11），与兼容字段 `status=accepted` 不是一回事。  
改进点记入时**不预分应用层**；L1/L0 只在抽象后的手册上判定。

**目标导入编排**见 §12（四步蓝本）；与现行链并存时，同书重导仍只跑 L3。

## 3. 记什么 / 不记什么

| 记 | 不记 |
|----|------|
| 对准意图、页面真改对、有成功步骤的**一件事** | 失败 / 放弃 / 中间废稿 |
| 同页多项成功 → **多条都留** | 只做了动作、未兑现客户意图 |
| 同一件事翻修成功 → **按事项覆盖**（近义摘要键） | 测试探针、流水线事件 |

禁止仅因「同一页 / 同一粗位点」就只留最后一条。

## 4. 资产层级（与「下次导入」关系）

| 层 | 含义 | 载体 | 同书再导 | 跨书 |
|----|------|------|----------|------|
| L0 | 全库流水线 | Python normalize/renderer + pytest | ✅（重建镜像后） | ✅ |
| L1 | 教材族**生效面** | `layout_rules.yaml`、`overrides/pages/`、`config.*`（需 publish）、theme CSS | ✅ | ✅（同 edition 族） |
| L1′ | 教材族**账本** | `EditionCorrectionQueue`、`policy.entries` | ❌ 只记账 | ❌ |
| L2 | 专书绑定 | `corpus_book_edition_bind` | 绑定关系 | — |
| L3 | 专书页补丁 | `corpus_corrections`（必有 `ip_id`） | ✅ 指纹匹配且 **after 非空** | ❌ 不可抄块 ID |
| L3′ | 旧任务**最终 IR** | 上一次导入的 `blocks.json` | ✅ 页级继承（`inherit-pages`） | ❌ |
| L4 | 本 job 过程 | 会话 attempt / layout_change_log | ❌ | ❌ |

**同书下次导入的默认路径是 L3 回放**，不是「必须先落到 L1」。  
L1/L0 是抽象升级后的确定性挂钩；`patterns.route` / `land` **只记账**，写盘靠 `fs.request_edit` / `editions.publish` / 仓库补丁+重建镜像。

`policy.entries` 不影响 MinerU 全书导入（见 `doc/mineru-fullbook-import-flow.md` §2.2）。

**L4 不能当回放源**：会话账本 `layout_change_log` 只存 `tool` + `args_summary`（截断的自然语言摘要）
+ 页号 + 块 ID，**没有工具真实参数**，照它「复现 AI 对话里的操作」只能让模型再改一遍（不确定）。
要还原上次成果就用 L3（页补丁）或 L3′（页级继承）。

L3 现在有两条护栏与一条根因修复，详见 `doc/mineru-fullbook-import-flow.md` §8.6（5）：
`after` 为空时 confirm 会自动从任务 IR 取该页快照；整页替换块数落差过大会跳过而不是削页。

## 5. 表与字段

### 5.1 `corpus_improvement_points`

| 字段 | 说明 |
|------|------|
| id | UUID |
| fingerprint_hash | 书指纹 |
| source_job_id / source_session_id | 任务 / 会话 |
| locus_key | `page:{n}:cat:{c}:slot:{s}`（粗位置；同页可有多条） |
| page_no / category / authority | 页、类目；页改进用 `edition`（`pipeline` 不进表） |
| status | **兼容字段**：进表写 `accepted`；**不是**整理状态 |
| **prep_status** | **整理状态**：`needs_triage` \| `needs_split` \| `needs_aggregate` \| `ready`（旧 `needs_conflict_resolve` 读时归一）；见 §11 |
| **origin** | 闭集：`ai_auto` \| `user_ask` |
| **summary** | 直奔主题；勿写「记入待审」；勿堆内部黑话 |
| kind | 可选细类（可空） |
| **needs_split** | 多主题待拆标志（0/1）；与 `prep_status=needs_split` 应对齐 |
| claim_json / evidence_json | 主张与 attempt 摘要 |
| procedure_json | `{steps, verify, avoid}`；须通过成功路径校验 |
| concrete_json | 专书页补丁载荷；内可含 **`lineage`**（改进历程事件列表，见下） |
| pattern_id / pattern_status | 抽象轨：默认 `none`；仅完成体可抽象 |
| superseded_by / merged_into_id | 历史指针（旧冲突路径；prep 聚合以 lineage 为准） |

**`concrete_json.lineage`（改进历程 / 来历）**：拆分、聚合在**硬删旧条之前**写入存活条的摘要事件（`op` / `at` / `summary` / `parent` 或 `peers` / `meta`）。列表接口顶层亦返回 `lineage`。  
Studio 右栏：**「改进历程」**讲后来怎么整理出来的；**「完整生命周期」**讲当初怎么产生的（用户→AI→工具→结果）。二者并存、不互相替代。

**旧来源码（已迁完，勿再写入）**：`fullbook_ai`→`ai_auto`；`agent_page`/`human_confirm`/`migrated_l3`→`user_ask`；探针/流水线事件→删除。

Studio 列表（现行）：**页 · 来源 · 类别 · 摘要 · 删除**。  
Studio 列表（目标）：增加 **整理状态**；对 `ready` 提供 **抽象** 按钮。

**硬删**：`DELETE /improvement-points/{id}`。  
**批量清无路径**：`POST /improvement-points/purge-without-procedure`。

### 5.2–5.4

`corpus_improvement_links`、`corpus_book_edition_bind` — 关系与专书绑定。

### 5.5 `corpus_improvement_patterns`（抽象轨）

| 字段 | 含义 |
|------|------|
| category / abstract_text | 类目与判据式摘要 |
| **suspect_type** | 症状名（全大写下划线，闭集）。**跨书归并的锚点** |
| **detector_ref** | 指向 `configs/checks/` 里的哪份检测器声明（YAML 文件名） |
| **remediation_kind** | `rule`（能自动修）/ `playbook`（只能按手册修） |
| **playbook_ref** | `playbook` 档必填的手册**指针**，形如 `configs/checks/x.yaml#remediation.playbook`；步骤正文在那份 YAML 的 `remediation.playbook` 里，读的时候由 `corpus_symptoms.resolve_playbook()` 解析成全文（接口另给 `playbook` 字段）。改 YAML 即时生效，库里不存第二份步骤 |
| **fingerprints_json** | 成员的症状指纹集合，用于精确复用历史「改前 → 改后」 |
| **hits** | **检测器命中处数**，仅由导入/重建时的检测累加（`enrich_findings(count_hits=True)`）。读接口反复富化不计数 |
| **true_positive / false_positive** | 人工判定次数（确认真问题 / 判为误报）。**人工反馈不动 `hits`**，否则误报率分子分母一起涨，永远看不出检测器在变吵 |
| **book_count** | 跨了几本书，晋级的核心指标（从真实成员重算） |
| landing_target | 闭集，见 §8；**新模式不再按类目猜先验，留空即未定** |
| landing_note / target_layer | 定路由理由；粗分层只读兼容 |
| status | draft → candidate → routed → landed；或 no_landing |
| member_count / landed_ref | 成员数（**从真实成员重算，不做增量累加**）；落地引用（**land 不写盘**） |

清理后旧模式可能仍在且 **0 成员**——**不得当路由真理**。须从 `ready` IP 重抽象。

### 5.5.1 一个模式 = 四元组

一条模式由四样东西定义，缺一样就不是可复用模式：

| 名字 | 是什么 | 存在哪 |
|------|--------|--------|
| **症状**（suspect_type） | 可命名的问题类型，如「页眉被当成正文」 | 模式表 + YAML 同名 |
| **检测器**（detector） | 可执行的判断条件，命中即这一页有此症状 | `learning-buddy-api/configs/checks/<name>.yaml` |
| **处置**（remediation） | `rule` 自动修 / `playbook` 按步骤修 | 模式表 |
| **生效面**（landing_target） | 修法应该写在哪一层，见 §8.2 闭集 | 模式表 |

有了 `detector_ref` 这一维，三类模式才分得开：

- 能查也能修 = 有检测器 + `rule`
- 能查但修不了 = 有检测器 + `playbook`
- 既查不出也没定型 = 两者都空，留作单书补丁

这是**新增一个维度**，不是新增落位；原有六个 `landing_target` 闭集不动。

### 5.5.2 `corpus_symptom_false_positives`（误报库）

| 字段 | 含义 |
|------|------|
| suspect_type / fingerprint | 哪条症状、哪个指纹被判「这不是问题」 |
| pattern_id / job_id / fingerprint_hash | 关联模式、导入任务、书 |
| page_no / block_id / evidence_json / note | 位置、当时证据、人写的理由 |

**只降权不屏蔽**：同指纹下次仍显示，但严重度降一档并标注「疑似历史误报」。
没有这条反向通道，检测器只会越来越吵，永远拿不到收敛信号。

### 5.5.3 晋级门（playbook → rule）

`POST /patterns/{id}/promotion-check` 判五道门，**缺一不可**：

1. 复现 ≥ 3 次且跨 ≥ 2 本书
2. 成员的修复动作完全一致（procedure 里用的工具唯一）
3. 可形式化（有 `detector_ref`，且生效面不是 `L3.book_patch`）
4. 正反例全绿（正例召回 100%、反例误报 0）
5. 误报库无冲突（人工判误报数 ÷ 检测器命中数 ≤ 20%；命中数 = `hits`，只由导入时的
   检测累加，所以这一关在这条检测器真正跑过新书之后才有意义）

**只出提议，不写任何规则文件。** 判断可以自动，落笔必须经人——规则一旦落进
`layout_rules.yaml` 就会默默改变全书渲染，出错时没人知道是谁改的。

### 5.6 `corpus_corrections`

`ip_id`、`procedure_json`、`after_json` / `before_json`。  
回放：仅 accepted IP 且 **after 非空**；空 after 跳过。

## 6. procedure 形状

```json
{
  "steps": [{"tool": "patch_block", "args_summary": "…", "at": "ISO"}],
  "verify": "final_html|user_confirm",
  "avoid": [{"tool": "…", "note": "earlier attempt"}]
}
```

成功路径：至少一步「真实 tool + 非空 args」，或 after 块非空。

## 7. API（admin，经 Studio `/proxy/*`）

| 方法 | 路径 |
|------|------|
| POST | `/improvement-points/ingest-attempts` |
| POST | `/improvement-points/confirm` |
| POST | `/improvement-points/converge` |
| DELETE | `/improvement-points/{id}` |
| POST | `/improvement-points/purge-without-procedure` |
| POST | `/improvement-points/{id}/resolve-conflict` |
| POST | `/improvement-points/{id}/pattern` |
| GET | `/improvement-points` |
| GET/POST | `/patterns`… `/route` `/land` `/merge` `/split` |
| POST | `/patterns/{id}/bind-detector` — 绑症状名、检测器、处置档位 |
| GET | `/patterns/{id}/promotion-check` — 五道晋级门，只出提议 |
| GET | `/checks` — 生效中的检测器清单 + 可用词表 |
| POST | `/checks/dry-run` — 在真实页上试跑一条检测器（不落库） |
| POST | `/checks/run-examples` — 跑正反例，量召回率与误报率 |
| POST | `/checks/false-positive` — 记一条「这不是问题」 |
| GET | `/jobs/{job_id}/symptoms` — 这次导入的已知症状 / 未知疑点 |
| GET/POST | `/books/{fingerprint_hash}/bind-edition` |

（目标）拆分确认、整理状态流转、抽象三层视图、L0 建议草稿与末段确认 —— 实现时另增端点，本文先定语义。

## 8. 抽象轨与六种落地目标

入库**不预分应用层**。抽象默认 **L3.book_patch**。能写成导入挂钩才定 `landing_target` 并**另行写盘**。

**硬闸**：仅 `prep_status=ready` 可点「抽象」或挂模式成员。

### 8.1 模式生命周期

```text
draft → candidate → routed → landed
                 ↘ no_landing
```

- `patterns.route` / `land`：**只改模式表**  
- 验证：`rebuild_ir` + 成员页 diff；escalate 不恶化  

### 8.2 `landing_target` 闭集

| 取值 | 写到哪 | 导入时谁跑 | 注意 |
|------|--------|------------|------|
| `L3.book_patch` | 不落规则 | L3 回放 | **默认** |
| `L1.layout_rule` | `layout_rules.yaml` | `decide_page` 赋 zone | 只能 zone |
| `L1.config_pattern` | `config.json` | 适配器 | **须 publish** |
| `L1.theme_css` | `assets/theme.css` | 挂 CSS | 只观感 |
| `L0.normalize` | Python + pytest | 归一化 | **重建镜像** |
| `L0.renderer` | Python 渲染 | `render_page_html` | **重建镜像** |

### 8.3 抽象按钮 → 三层呈现（目标）

Studio 对选中的 `ready` IP（可多选）点 **抽象** 后，呈现：

1. **生效面分区**（上表六类大 Group）  
2. **抽象点 / 手册**（同类集合标题）  
3. **具体改进点**（成员 IP + procedure）

技能：`buddy-copilot/agent-env/skills/pattern-abstract.md`。

### 8.4 谁来写模式（Cursor SDK Agent）

改进点页的 **「抽象成模式」** 按钮起 `pattern_abstract` 会话，由 buddy-copilot
分批推进（一轮一批，默认 25 条，见 `buddy-copilot/app/pattern_batch.py`）。
Agent 每组走五步闭环：

**归症状 → 写检测器 YAML → 配正反例 → 自验迭代 → 定档记账**

三条硬约束决定了方案只能是这个形态：

- Agent **不能改 `learning-buddy-api/app/`**（受保护根目录，改了整轮回滚）。
  所以检测器只能是声明式 YAML，写在 `configs/checks/`（已 bind-mount，改完
  `rebuild_ir` 即时生效）。骨架归人、内容归 Agent。
- 单轮预算 900 秒 / 120 次工具调用 / 12 个文件，**超限整轮回滚**，所以必须分批。
- `when` 里**只准写结构和几何特征**；绝对页码、块 ID、具体文字会被服务端拒绝加载
  （见 `corpus_symptoms._cross_book_errors`）——写了这些换本书就失效。

第 4 步是关键：YAML 改完即时生效、正反例测试 Agent 自己能跑，所以它能自主收敛，
不需要人陪着调参。

#### 词表分两层（2026-08-12）

| 层 | 内容 | 改它要付什么代价 |
|----|------|------------------|
| 原子词 | `zone` `width_ratio` `rel_y0` 等 65 个字段 + 14 个函数，取自页面几何与判定结果 | 写在 `app/services/corpus_symptoms.py`，Agent 禁改，加一个要改代码并重建镜像 |
| 派生特征 | `configs/checks/features.yaml`，用原子词组合出的命名特征，可引用彼此 | 挂载生效，Agent 可写，改完 `rebuild_ir` 即时生效 |

绝大多数新症状是老词的新组合，所以先在派生特征层解决；组合不出来（需要新数据源，
例如图片像素、跨页关系、资源是否存在）才提原子词需求。

`when` 和 `evidence` 里的名字**在加载期就要全部落在词表内**，写错当场报错并提示正确
名字。这条以前是缺的：拼错一个字母的检测器会在求值期抛 `NameNotDefined`，被逐页的
兜底 `except` 记成一行日志，于是它永远不命中，而清单里还显示 enabled。同理，没能加载
的声明现在会出现在 `GET /checks` 的 `load_errors` 里，不再只写日志。

派生特征另有两条加载期硬约束：不能互相引用成环（否则求值递归到栈溢出），引用了块级
字段的特征不能用在 `scope: page` 的检测器里（页级求值时根本没有块）。

### 8.5 首轮抽象结果（必修二，2026-08-11）

- **361 条改进点全部有归属**（原 343 条 + 4 条多主题批量记录拆出的 22 条子条）。
  归到 **59 个有成员的模式**，其中 18 个带检测器 YAML。
- 处置档位**全部是 `playbook`**：晋级门里的「复现 ≥3 次且跨 ≥2 本书」这一关只有
  一本书的证据，过不了；其余四关（修复动作一致、可形式化、正反例全绿、误报库无冲突）
  已通过。这正是设计意图——**不拿单书证据造自动改写规则**。
- 跨书验证：18 条检测器在**必修一**（另一本书，job `d8a02e41…`）上 **12 条命中、
  共 148 处**，每处都带模式与手册。这是「换一本书用得上」的直接证据。
- 降噪一轮：四条过宽检测器（章眉、缺题干、多图缺标、超长公式）用必修一的误报做反例
  收紧后，必修一命中 58→3、80→10、74→5、178→10，正反例仍全绿，severity 降为 low。

**已知口径差**：流式导入阶段跑出的命中数会略低于 `rebuild-ir` 之后的数，因为部分跨页
归并还没做完。以导入完成后面板加载的那份为准。

**2026-08-12 复核两处修正**（抽象结果本身无误，全 `playbook` 是上面那一关的必然结果）：

1. `playbook_ref` 存的是指针而非步骤，Agent 与界面此前拿到的是一个路径。现改为**读取
   侧解析**：`resolve_playbook()` 从检测器 YAML 取步骤全文，接口多给 `playbook` 字段，
   Studio 显示步骤、指针降为「出处」，`checks` 清单也带上步骤。库里仍只存指针，不会
   出现两份步骤打架。
2. `hits` 原先只在人工上报误报 / 确认真阳时 +1，导入命中并不计数，「命中/误报」列因此
   恒为 0/0、第五道门空过。现改为导入检测累加 `hits`，人工反馈只记
   `true_positive` / `false_positive`；界面列名改为「检测命中/误报」。已入库的旧模式
   `hits` 仍为 0，下一次导入或 `rebuild-ir` 起开始积累。

## 9. 已砍除 / 勿再用

| 旧物 | 状态 |
|------|------|
| Studio「有效/旧版/应用层/未来作用」生命周期列 | 已去掉（**整理状态**是另一列，见 §11） |
| 来源多值 | 已闭集 ai_auto / user_ask |
| confirm 自动建模式桶 | 默认 none |
| 按粗位点整页覆盖多事项 | 禁止 |
| 无 ip_id 的 correction 回放 | 禁止 |
| `policy.entries` 当 MinerU 规则出口 | 无效 |
| 系统内迁移模块 | 永不做 |
| 导入时 LLM 现写现 exec L0 代码 | **禁止**（L0 走仓库补丁，见 §14） |

## 10. 验收（硬 · 现行）

1. Studio：页/来源/类别/摘要/删除；来源仅两类。  
2. SQL：`origin IN ('ai_auto','user_ask')`。  
3. 新记入不自动建模式；无成功路径拒绝。  
4. corrections 必有 `ip_id`；回放依赖非空 `after`。  
5. 硬删无残留。  
6. 禁止用单测内存库代替生产验收。

（目标验收见 §15。）

---

## 11. 整理状态（prep_status）— 预抽象流水线

**已实现（整理入口 + 完备路径拆分）**：入库一律 `needs_triage`（待整理）；「整理」「拆分提案」由 **Cursor SDK Agent**（经宿主机 `buddy-copilot` `:8200`，场景 `ip_triage` / `ip_split`）完成；`buddy-api` 只做硬闸与落库（`triage-apply`、完备 `split-propose`/`split-apply`、聚合裁决、非 ready 挂 pattern → 403）。**不是** buddy-api 直调 vLLM。

Studio：进度条 ①待整理 → ②待拆分 → ③需聚合 → ④完成体；主按钮「整理」开 Copilot；「拆分」「处理聚合」进工作台，确认后写库。详规见 `doc/plans/ip-block-a-prep-status.md`（历史 Block A 基线；分类/拆分推理已迁至 Copilot）。

与兼容字段 `status=accepted`（进表即有效）分离。界面文案：**整理状态**。

| 值 | 含义 | 可抽象？ |
|----|------|----------|
| `needs_triage`（待整理） | 刚进表，尚未经整理分析 | 否 |
| `needs_split`（待拆分） | 复合改进，须按成功路径切开 | 否 |
| `needs_aggregate`（需聚合） | 与其它点对立，先聚簇再裁决（读旧码 `needs_conflict_resolve` 时归一） | 否 |
| `ready`（完成体） | 无需再拆、无未决对立簇、有成功路径 | **是** |

```text
confirm → 一律 needs_triage
  → 人点「整理」→ Cursor Agent（ip_triage）→ triage-apply
       → ready | needs_split | needs_aggregate(+簇)
  → 待拆分：Agent（ip_split）或规则快路径提案 → 人确认 → split-apply
       （子条默认再进 needs_triage；完备 procedure + after 全覆盖互不重叠）
  → 需聚合：工作台裁决 → aggregate/conflict-resolve → ready
  → 仅 ready 可点「抽象」→ 三层结构（§8.3）
```

- **整理 / 拆分提案**：Cursor Agent；api 校验后写库。  
- **拆分确认（split-apply）**：建子条时写入父条摘要到各子条 `lineage`，再硬删父条。  
- **聚合**：对立簇 + 人勾选保留/删除；**删之前**把被删方摘要写入保留条 `lineage`，再硬删。  
- **人在环**：Agent 不静默改完成体集合；不完备提案禁用确认。

`needs_split` 列（0/1）应与 `prep_status=needs_split` 保持一致（双写）。

---

## 12. 四步导入蓝本（目标产品流）

对表现行 MinerU 链，不另起 OCR 引擎。短链见 `doc/mineru-fullbook-import-flow.md` §8.7。

| 步 | 角色 | 要点 |
|----|------|------|
| **1 基础识别** | 系统 | MinerU → IR；产出可分析的「未优化全书底稿」 |
| **2 自动选积木** | **AI** | 分析底稿 → 从**已固化模块目录**自动组装**蓝本（Blueprint）**；可选向量相似书先验；用户可轻量改选，**默认不靠人手搭积木**；选中后**确定性执行** |
| **3a 初渲** | 系统 | 蓝本 + L3 回放 + render → HTML |
| **3b 二次施工** | AI + 向量 | 初渲仍差的页：检索手册/IP 施工；不静默 exec 代码 |
| **4 对话闭环** | 用户 + AI | **检索优先**（下）→ confirm → 回灌；**末段 L0 汇总确认**（§14） |

**Step4 检索优先（已锁定）**：

```text
用户提出改页要求
  → 先检索「全部完成体/有效 IP」向量库（summary + procedure）
  → 命中：按已成功步骤复放或改编
  → 未命中：再自提新方案
  → confirm → 新 IP 回灌向量库
```

禁止默认跳过检索直接发挥（用户显式要求除外）。  
同书重导：只跑 L3，跳过 Step2/3b/4 与 L0 确认。

**落盘判定树**：

- AI 能映射已固化模块 → 进蓝本（确定性，可回归）  
- 不能模块化但同书可复现 → L3 `after`  
- 跨书反复成功且可规则化 → 固化新模块（供以后 Step2 再选）  
- 其余 → Step3b/4 检索施工，不强行发版  

`layout_rules`/theme 改文件即可用；config 须 publish；L0 须补丁+重建镜像。

---

## 13. 经验知识库（三级）与检索

| 级 | 名称 | 载体 | 谁消费 |
|----|------|------|--------|
| 1 | 生效面分区 | `landing_target` 枚举 | 过滤/路由，不作唯一向量簇键 |
| 2 | 抽象点 / 手册 | pattern + 向量 | Step2/3b |
| 3 | 具体改进点 | IP + procedure + 向量 | **Step4 首查** |

- 引擎：复用 `learning-buddy-api` 已有 **Milvus** 检索增强（RAG），不新起向量产品。  
- 主索引建议：`prep_status=ready`（及已抽象成员）的 summary + procedure。  
- 混合闭环：确定性挂钩能跑则跑；检索负责跨书选案与对话施工，不替代已落盘规则。

---

## 14. L0：双阶段提案 · 末段集中确认

对象：仓库内 `normalize` / `renderer`（及测试）。  
执行模型：**补丁包** + pytest + 重建 `buddy-api`/`buddy-worker` 镜像 + **一键回退**快照。  
**禁止**导入 worker 内 LLM 现写现 exec。

| 阶段 | 何时 | 产出 | 用户确认？ |
|------|------|------|------------|
| **提案 A** | Step2 前后：对全书**底稿**分析 | 写入 L0 建议草稿集合 | 否 |
| **提案 B** | Step3 HTML 出后：AI 分析改进 | 追加/修订同一集合 | 否 |
| **确认 C** | Step4：全书 AI 改页告一段落的**最后阶段** | 在 **AI 对话里弹出汇总包** | **是**：集中否决/采纳/改写/执行 |

汇总包含：建议列表、来源阶段（底稿/渲后）、关联页、风险、是否已有同类 recipe。  
执行后留痕（job/指纹/diff/测试/操作者）供复现；效果差则一键回退。

---

## 15. 目标验收口径（设计落地后）

1. 整理未到 `ready` 不能抽象。  
2. Step2 默认 AI 选积木；选中后确定性执行。  
3. Step4 默认先查全部 IP 成功经验再自创。  
4. L0 仅末段集中确认，且可回退；无导入时热执行生成码。  
5. 同书重导不依赖模型（L3）。  
6. 抽象呈现三层：生效面 → 手册 → IP。

---

## 16. 架构图索引（Canvas）

| Canvas | 内容 |
|--------|------|
| **`ip-rag-four-step-blueprint`** | **综合目标蓝图（首选）**：四步 + 整理状态 + L0 双阶段/末段 + 检索优先 |
| `landing-surfaces-target-flow` | 六生效面 · 日常/抽象/下次导入 |
| `landing-execution-audit` | 六挂钩谁真跑 |
| `landing-targets-review` | 要不要「全交给 AI」 |
| `import-ip-edition-pipeline` | 导入→IP→方案接线 |
| `system-topology-import-edition-improvement` | 服务拓扑现状 |
| `edition-architecture-current-vs-target` | 方案架构现状/目标 |

以后实现优先级（不在本文编码）：① `prep_status`+拆分/矛盾+抽象三层 → ② 模块目录+Step2 → ③ IP 向量+Step4 硬顺序 → ④ L0 草稿+末段汇总+补丁回退 → ⑤ 手册向量+Step3b。
