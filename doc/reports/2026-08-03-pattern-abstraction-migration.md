# 抽象轨全量整理对账报告

> **历史快照（2026-08-03）**。后续 IP 清理已解绑成员；勿用本文结论驱动当前落地。  
> 权威：`doc/improvement-point-management.md`。

日期：2026-08-03 · 范围：250 条 accepted 改进点 / 141 个抽象模式（教材 `hujiao_math_hs_v1`）

## 结论先行

原来「抽象」这件事没有真正发生：141 个模式的 `target_layer` **全是 `L1`**、
`status` **全是 `draft`**、`landed_ref` **一条没有**，94% 是单成员。
整理后 18 条已定路由、3 条判为不可规则化、18 条查明拆不动（根因在确认轨）、
其余 98 条带类目先验待逐条确认。**最重要的产出不是数字，是确认了原诊断成立**：
`layout_rules.yaml` 只能表达 zone，而真正能写成 zone 规则的只有 15 条。

## 整理前后

| 指标 | 整理前 | 整理后 |
|---|---|---|
| 模式总数 | 141 | 137（合并与拆分净减 4） |
| `target_layer` 分布 | `L1` 141（100%） | 由 `landing_target` 派生，见下表 |
| `status` 分布 | `draft` 141（100%） | routed 18 · no_landing 3 · needs_ip_split 18 · draft 98 |
| `landed_ref` 非空 | 0 | 0（本轮只定路由，未落盘） |
| 单成员模式 | 133 / 141（94%） | 126 / 137（92%） |

### 落地目标分布（整理后）

| landing_target | 条数 | 说明 |
|---|---|---|
| `L1.config_pattern` | 34 | 识别正则与几何阈值 |
| `L0.normalize` | 33 | 文本归一化（CJK 混进 LaTeX 等） |
| `L1.theme_css` | 21 | 纯排版观感 |
| `L1.layout_rule` | 15 | **真正能写成 zone 规则的只有这些** |
| `L0.renderer` | 9 | 块级重组装、阅读序 |
| `L3.book_patch` | 3 | 不可规则化 |
| （未定） | 22 | 其中 18 条是 `needs_ip_split` |

**原诊断得到证实**：整理前 141 条全被标成 `L1`，整理后只有 15 条（约 11%）真的属于
`L1.layout_rule`。也就是说即便聚类做到完美，按旧词表也有近九成模式无处可落。

## 三个具体发现

### 一、分类 bug 影响 36 条

`infer_category` 原先首次命中即返回，而 `caption` 同时出现在 `figure`（声明第 1 位）
和 `table`（第 4 位）的关键词里，**所有表格标题问题都被误判成 figure**。
改成打分匹配后 36 / 141 条类目发生变化，其中 `figure → table` 5 条、
`figure → math` 7 条、`figure → title` 4 条。

### 二、Agent 推翻了类目先验，且推翻得对

按类目填的先验只是起点。Agent 逐条看摘要后的几个改判很说明问题：

- 「练习题干补全后再接（1）（2）列表」：先验 `L1.theme_css` → 改判 `L3.book_patch`。
  题干文本本身缺失，CSS 补不出内容。
- 「补回缺失页眉『6 三角』」：Agent 抽查 p17 的中间表示，发现块「6 三角」**已经在页顶**，
  只是 `type=paragraph` 且没有 zone。所以这不是「缺内容」而是「没进 running 槽」，
  判 `L1.layout_rule` 成立。摘要的字面描述是误导的。
- 自动标注的「多主题」有一批是**假拆分**：如「①②→全角（n）」与「array 内保持半角」
  被分句器判成 exercise + math 两个主题，实际同属文本归一化，Agent 合回一条。

### 三、18 条拆不动，根因在确认轨

`patterns.split` 搬的是**成员**，而这 18 条只有一个成员，搬空了源模式就没了——
工具拒绝这种操作是对的。它们长这样：

> 必修二第4批：删装饰图；第7章标题；负号一→−；或出公式；习题7.1题干①②③④

一次确认记了 4 件互不相干的事，落地目标分属 4 个不同生效面。这在模式层无解，
标为 `needs_ip_split`，**根因是确认时的输入形状**：`confirm_improvement` 接受自由文本
`summary`，没有约束「一条改进点只说一件事」。

**建议的永久修复**（本次未做，属确认轨范围）：确认接口要求按 locus 逐条提交，
或在服务端按分句拆成多条 IP。不修这个，抽象轨会持续收到多主题输入。

## 遗留

- 98 条 `draft` 带类目先验但未经确认，需继续跑 `pattern_abstract` 会话逐条定路由
- 已定路由的 18 条尚未落盘：`L1.layout_rule` 的 15 条需草拟 YAML 走 `fs.request_edit`，
  `L0.normalize` 的需补 pytest，落地前都要全书 `rebuild_ir` 回归
- 数据里混进一条流水线噪声模式「检测到越权文件改动（已回滚）」，属 `authority=pipeline`
  不该进抽象轨，待清理

## 操作记录

- 备份：`data/backups/ip-patterns-20260803-062440.sql`（含 patterns / points / links 三表）
- 确定性整理：`tools/pattern-migrate.py --apply`（重分类 + 填先验 + 标多主题）
- 语义整理：buddy-copilot `pattern_abstract` 会话 ×2

第二次会话被 `fs_guard` 判定越权终止 —— 起因是整理期间有人在同一工作区改
`doc/improvement-point-management.md`，快照守卫把它算到了 Agent 头上。
**教训：Copilot 会话运行期间不要动工作区文件。** 会话终止前的路由结果已落库，未受影响。
