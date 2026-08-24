# Skill: 改进点抽象（pattern-abstract）

把这一本书的施工记录，变成**换一本书还能用**的模式。

一条改进点写的是「第 12 页那张图挪到了侧栏」。这只对这一页有用。抽象要回答的是：
**这是什么毛病、怎么认出来、上次怎么修好的、修法该写在哪一层。**

字段与闭集以 `doc/improvement-point-management.md`（§8、§11）为准。

## 一个模式 = 四样东西

| 要素 | 是什么 | 落在哪 |
|---|---|---|
| **症状**（suspect_type） | 可命名的问题类型，如 `HEADER_IN_MAIN` | 模式表字段，跨书对齐的锚点 |
| **检测器**（detector） | 一段可执行判据，命中即有此症状 | `learning-buddy-api/configs/checks/*.yaml` |
| **处置**（remediation） | `rule` 能自动修 / `playbook` 只能按手册修 | 模式表字段 |
| **生效面**（landing_target） | 修法写在哪一层 | 模式表字段，闭集见下 |

四样缺一样都不完整：没症状名就跨不了书，没检测器就查不出来，没处置就修不了，
没生效面就落不了地。

## 五步闭环

每一组走完这五步，再开下一组。

### 1. 归症状

从 `tools`、`locus_key`、`category` 反推「这是什么毛病」，给一个 `SUSPECT_TYPE`
（全大写下划线）。同名才可能是同一个模式。

**不要照着摘要文本归类。** 摘要是施工记录，掺着页码和具体正文，照它归出来的
只会是「第 12 页那张图」这种一次性的东西。

### 2. 写检测器

在 `learning-buddy-api/configs/checks/` 建一份 YAML，格式和完整词表见该目录的
`README.md`，也可以用 `checks.list` 现取。

```yaml
suspect_type: HEADER_IN_MAIN
title: 页眉被当成正文
severity: medium
detect:
  scope: block
  when: "zone == 'main' and rel_y0 < 0.08 and char_count <= 30 and not has_math"
  min_hits: 1
evidence: [zone, rel_y0, char_count, text_preview]
remediation:
  kind: rule
  landing_target: L1.layout_rule
  rule_ref: "editions/<id>/layout_rules.yaml#header-band"
  locate_by: [zone, rel_y0, text_preview]
examples:
  positive: [tests/data/checks/header_in_main_pos.json]
  negative: [tests/data/checks/header_in_main_neg.json]
```

**`when` 里只准写结构和几何特征。** 绝对页码、块 ID、具体正文文字一律禁止，
服务端会直接拒绝加载。判断一条 `when` 合不合格，问一句：**换一本教材，它还成立吗？**

`locate_by` 必填，是 `evidence` 的子集（2–4 个字段）：**下一个照着这条症状动手的人，
不给这几个字段就不知道该改哪一块**。整个 `evidence` 会把上下文撑爆，所以只挑真正
用来定位的那几个。

### 2b. 词不够用怎么办（不用重建镜像）

`when` 里能用的名字分层，`checks.list` 的 `vocabulary` 会告诉你每个词住在哪一层。
按这个顺序试，**走到最后一步之前都不用改 Python**：

1. **组合现有名字** → 写进 `configs/checks/features.yaml`（派生特征）。
2. **要一个新词** → 写进 `configs/checks/vocabulary.yaml`：名字 + 表达式 +
   中文 `doc` + 双极性 `examples`（加载时真跑，不过就不加载）。
   可用聚合算子：`any` / `all` / `findall` / `latex_texts` / `has_cjk` / `lexicon`，
   支持列表推导式。
3. **要一批中文例外词** → 写进 `configs/checks/lexicons.yaml`（带中文 `doc`），
   表达式里用 `lexicon('名字')` 取。表达式里**硬写中文正文会被跨书守卫拦掉**。
4. **一条表达式真写不出来** → 才写 `configs/checks/vocab_ext/*.py` 受限插件：
   - 草稿先写进 `vocab_ext/proposals/`（加载器不执行），用
     `vocab.check_plugin(path)` 跑正反例自检；
   - 全绿后用 `vocab.request_plugin_edit` 申请写进 `vocab_ext/` 顶层。
     这是**会在 buddy-worker 进程里执行的代码**，走独立批准闸，不和 YAML 共用批准。

格式与完整规则见 `learning-buddy-api/configs/checks/README.md`。

### 3. 配正反例

写进 `learning-buddy-api/tests/data/checks/`，一个正例（该报）一个反例（不该报）。
反例要挑**长得像但确实没问题**的页 —— 拿一张空白页当反例等于没测。

### 4. 自验迭代

- `checks.dry_run(job_id, yaml_text=...)` —— 在真实页上试跑，看它到底命中什么。
- `checks.run_examples(yaml_text=...)` —— 量召回率和误报率。

**召回不满 100% 或有任何误报，回第 2 步改 YAML。** 这个目录是挂载的，改完下一次
调用就生效，你自己能收敛，不需要等人、不需要重建镜像。

这一步是整套方法能成立的原因。跳过它，写出来的检测器只是看上去合理。

### 5. 定档记账

同指纹的修复动作**完全一致**才配定 `rule`；不一致就老实写 `playbook` 手册，
把步骤写进 `playbook_ref`。

- `patterns.bind_detector(pattern_id, suspect_type, detector_ref, remediation_kind, ...)`
- `ip.set_pattern(ip_id, suspect_type=...)` 把成员逐条挂过去

**检查项和修复规则分开记账**。这是 343 条里最大的教训：能查出来不等于能修好。

## 生效面闭集

**默认立场是 `L3.book_patch`。** 只有规律能写成确定性导入挂钩时才升级。

| landing_target | 写到哪（写盘另做） | 什么样的规律属于这里 |
|----------------|-------------------|----------------------|
| `L3.book_patch` | 不落规则 | **默认**；单次/难规则化；只靠同书回放 |
| `L1.layout_rule` | `layout_rules.yaml` | 几何/正则 → **zone**（只能分区，不能删块改文） |
| `L1.config_pattern` | `config.json` | 匹配式、几何阈值（**必须 publish**） |
| `L1.theme_css` | `assets/theme.css` | 纯观感 |
| `L0.normalize` | Python + pytest | 跨书文本/公式归一化（**重建镜像**） |
| `L0.renderer` | Python 渲染 | 跨书 HTML 结构（**重建镜像**） |

判 `L1.layout_rule` 前先确认它能写成「满足某几何条件的块判进某个 zone」——
规则语言只能表达 zone。不能的一定属于另外五个之一。

## 写盘边界

| 目录 | 能不能直接写 |
|---|---|
| `learning-buddy-api/configs/checks/` | ✅ 挂载，改完即时生效（含 `vocabulary.yaml` / `lexicons.yaml` / `features.yaml`） |
| `learning-buddy-api/configs/checks/vocab_ext/proposals/` | ✅ 草稿区，加载器不执行 |
| `learning-buddy-api/configs/checks/vocab_ext/*.py` | ⚠️ **专属闸**：只能走 `vocab.request_plugin_edit`，`fs.request_edit` 会被拒 |
| `learning-buddy-api/tests/` | ✅ |
| `learning-buddy-api/configs/editions/` | ✅ 挂载 |
| `learning-buddy-api/app/` | ❌ **受保护根目录，改了整轮回滚**，走 `fs.request_edit` |
| `learning-buddy-c2/` | ❌ 永久禁止 |

`patterns.route` / `patterns.land` **只记账**，不写盘。

## 预算

单轮 900 秒 / 120 次工具调用 / 最多改 12 个文件。**超限整轮回滚。**

所以一轮只做一批（默认 25 条），做完在报告里写清 `remaining`，等下一轮继续。
宁可少做一批也不要跑爆。

## 晋级

`playbook` 想升 `rule`，用 `patterns.promotion_check` 自检五道门：
复现 ≥3 次且跨 ≥2 本书、修复动作一致、可形式化、正反例全绿、误报库无冲突。

**晋级只出提议。** 规则文件必须经人批准后才写入 —— 规则一旦落进
`layout_rules.yaml` 就会默默改变全书渲染，出错时没人知道是谁改的。

## 前置硬闸

- 场景 `pattern_abstract`；**没有** `corpus.patch_block`（这里不修单页）
- **仅** `prep_status=ready` 可抽象；`needs_split` / `needs_conflict_resolve` 禁止挂成员
- 成员必须有成功路径（procedure 或非空 after）
- 发现成员其实是多主题，退回确认轨拆 IP，**不要**用模式级拆硬扛

## 禁区

- 对非 ready 的改进点抽象或并桶
- 检测器没跑过正反例就定 `rule` 档
- `when` 里写页码、块 ID、具体正文
- 新词不写中文 `doc` 或不配正反例（加载期就会失败，别浪费一轮）
- 明明能用 `features.yaml` 组合出来，却去写受限插件
- 为整齐强行并桶；表象像但根因不同的（删装饰图 ≠ 公式撇号）不要并
- 假设 `land` 后导入自动变好 —— 查文件/镜像是否真改了
- 用 `harness.run_golden` 当证据（对 MinerU 线是假验证）
- 判不准就**别升级**：保持 `L3.book_patch`
