# 从 DeepTutor 借灵感 —— 对照 ir-nested-bands-v1 的待解问题

2026-09-16。探查对象：`DeepTutor/`（HKUDS/DeepTutor）。关键结论：**DeepTutor 自己不做版面分析**——它同样把 MinerU 当上游，但在 MinerU 输出之上建了一条规整的五层管线：内容寻址缓存 → 不可变冻结 bundle → 块策略分类 → IR 映射（合并/题注/占位符）→ 金样回归。骨架在 `deeptutor/services/rag/pipelines/lightrag/` 和 `deeptutor/services/parsing/`。抽查核实：`_caption`（sidecar.py:87-95）、三分法类型表（block_policy.py:26-32）、provenance 逐文件 SHA-256（tests/fixtures/lightrag_bridge/mineru-v2-current/）均属实。

---

## 一、对照我们的四个待解问题

### 1. 题注粘错（图1.5 重复）→ 借「优先级链 + 宽松归一化」

DeepTutor 的 `_caption(item, *keys)` 是个纯函数：先取直接 `caption` 字段，没有就按优先级翻 `image_caption`/`chart_caption`/`captions` 列表取第一条非空，全没有就返回空串。配套契约（fixtures README 的 Frozen mapping contract 表）：**可选元数据脏了 → 归一化为空；资产引用坏了 → 整篇 fail**。「可选宽松、资产严格」的边界写成显式契约表。

能借的：
- 题注提取收敛成一个纯函数，脏数据降级为空串而不是带着错往下传——这正好支持我们「空就清空」的方向：宁可没有，不要错的
- 粘连切分（「（b）p 轨道图1.5…」拆开）DeepTutor 也没做，是明确空白，但插入点清晰：在这个 `_caption` 同等位置加一道清洗 pass（按「图1.5」「（b）」题首标记切）
- 题注与正文分离存放 + 占位符引用（`{{IMG:im3}}`），粘连问题被结构隔离

### 2. 块合并 → 借「累积缓冲 + 遇边界 flush」和占位符

`build_ir()`（sidecar.py:118-317）的模式：维护累积缓冲，遇到标题块就 flush 成一个大块；图/表/公式不塞进文本，而是生成 `{{IMG:..}}` 占位符插回文本流，对象存侧列字段；每块带 `self_ref="blocks.json#/{index}"` 回溯指针指回源块。

能借的：
- 合并器模式直接对应我们的收口——我们的边界信号是「栏切换/图带打断」，它是「标题」
- **占位符思路可解图标回归**：合并大块里用锚点引用图，而不是把图段丢掉——盒子树里对应「图盒子挂在段落行内的锚点」
- `self_ref` 溯源指针 = 我们 pieces 里已存的 members/bbox，思路一致，可以补一个统一的回链字段名

### 3. 图标/装饰图 → 借「三分法块策略」

block_policy.py：`FILTERED`（页眉页脚页码，丢）/ `PRESERVED_AUXILIARY`（aside_text 等，保留但归辅助）/ `PRESERVED_SEMANTIC`（正文语义，保留）。**未知类型宁留勿丢、计数上报**（`unknown_by_type` 账本驱动策略扩展）。还有 MinerU v1→v2 类型改名映射表，升级解析器不动策略代码。

能借的：
- 装饰小图标走 **auxiliary 通道**：不丢（治好本轮的图标回归），但标记为辅助、渲染靠边/小尺寸。尺寸启发式（小图→图标）DeepTutor 没有，要自己加，但可作为 classifier 插进同一框架
- 「未知类型保留+计数」可直接用

### 4. IR 与渲染 → 借坐标元数据和扁平层级编码

- `Rect`（reading/models.py:63-119）：归一化 0..1、左上原点、`clamped()` 裁角、`from_any()` 宽容解析（畸形返回 None 不炸）；文档级记 `bbox_attributes={"origin":"LEFTTOP","max":1000}`——坐标系原点和量纲写进元数据
- 嵌套层级用「标题栈 + parent_headings」扁平编码，序列化友好、金样可断言
- 渲染：归一化 rect ×100% 直接转 CSS 百分比（AnnotationLayer.tsx）；`<figure>+<figcaption>` 语义标签 + HTML 白名单清洗（markdown-display.ts:60-137，剥离 `on*`/`style`/`javascript:`）

### 5. 金样 → 借「provenance 三件套」，治我们的老账

这是最值得整套搬的。DeepTutor 金样 = **自造源文件 + 逐字节固定的解析器输出 + provenance 清单**（每文件 SHA-256、来源 commit、捕获时间、schema 版本）。测试先校验金样文件哈希（金样被篡改测试即失败），再走完整链路断言具体字段值。测试分两层：策略层断言「类型序列+计数账本+输入未被 mutate」，映射层断言「具体字段+资产字节相等」。

能借的（正好对上我第四轮以来的老账）：
- 「金样 note 全空、无人工确认记录」→ provenance 清单就是答案：谁收的、什么时候、源是什么、每个文件哈希多少，比一句 note 硬得多
- 「check.py 薄」→ 两层测试拆法：我们的 check_units 相当于策略层，还缺映射层（断言具体文本字段，比如 chrome 题头文字、图 1.5 题注归属）
- 「金样被手改漂移」→ 逐文件 SHA-256 进清单，改了立刻红

---

## 二、DeepTutor 明确没做、我们要自研的

1. 题注粘连清洗（插入点已知，逻辑自研）
2. 阅读序纠偏（它把顺序权威让渡给上游；我们已做，建议保持「独立 pass、顺序变更可审计」）
3. 行内图标尺寸启发式（插进块策略框架即可）
4. 真嵌套盒子树（它的 IR 是扁平块列；我们的 bands 树自建，但标题栈编码和坐标元数据可抄）

---

## 三、移植优先级建议

1. **provenance 清单**（半天）：四页金样各配一份，SHA-256 + 捕获时间 + 一句「已肉眼对过 E」。直接终结「自我循环无记录」
2. **图标走 auxiliary 通道**（治本轮回归）：不丢、标辅助、渲染小尺寸
3. **题注 `_caption` 式纯函数 + 空就清空**：治图 1.5 重复
4. **check.py 拆两层**：策略层（现有）+ 映射层（具体文本断言：chrome、题注归属）
5. 占位符锚点：合并大块内联引用图，为「图仍走原格子」之外的第二种图挂载方式留路
