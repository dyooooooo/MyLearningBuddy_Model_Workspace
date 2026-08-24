# 跨书症状检测器（configs/checks）

这里放**跨教材通用**的症状检测器声明。一个文件一个症状。

- 教材专属的版式规则放 `configs/editions/<id>/layout_rules.yaml`，不要放这里。
- 单页修正放 `configs/editions/<id>/overrides/pages/NNNN.yaml`，不要放这里。
- 这个目录是 bind-mount 进 buddy-api / buddy-worker 的，改完**不用重建镜像**，下一次
  `rebuild-ir` 即时生效。

## 文件格式

文件名用小写症状名，例如 `header_in_main.yaml`。

```yaml
suspect_type: HEADER_IN_MAIN        # 症状名，全大写下划线，跨书对齐的锚点
title: 页眉被当成正文                # 人话一句，给界面显示
severity: medium                    # high | medium | low
enabled: true

detect:
  scope: block                      # block = 逐块判定；page = 整页判定一次
  when: "zone == 'main' and rel_y0 < 0.08 and char_count <= 30 and not has_math"
  min_hits: 1                       # 同页命中几个块才算这一页有这个症状

evidence: [zone, rel_y0, char_count, text_preview]

remediation:
  kind: rule                        # rule = 能确定性修 | playbook = 只能按手册修
  landing_target: L1.layout_rule
  rule_ref: "editions/<id>/layout_rules.yaml#header-band"
  locate_by: [zone, rel_y0, text_preview]   # 见下节
  playbook: |                       # kind 为 playbook 时必填
    1. 用 corpus.get_blocks 看这一页的块几何
    2. ...

examples:
  positive: [tests/data/checks/header_in_main_pos.json]
  negative: [tests/data/checks/header_in_main_neg.json]
```

## 硬约束

`when` 表达式**只准写结构与几何特征**。

禁止出现绝对页码、块 ID、具体正文文字。写了这些，换一本书就失效，
这条检测器也就失去了存在的意义。

判断一条 `when` 合不合格，问一句：**换一本教材，它还成立吗？**

`when` 和 `evidence` 里的名字必须都在词表内，**拼错会在加载期直接报错**并提示正确
名字。不要指望「先发出去再看看」——过去写错一个字母的检测器只会安静地永远不命中。

## `locate_by`：改进器凭什么定位

`remediation.locate_by` 是 `evidence` 的一个子集：**不给这几个字段，照着手册改的人
就不知道该动哪一块**。

- 缺省等于整个 `evidence`；校验强制它是 `evidence` 的子集。
- `kind: playbook` 时不得为空（`evidence` 也空着的话请先补 `evidence`）。
- 建议 2–4 个字段。整个 `evidence` 动辄十几项，几十处症状乘上去会把改进器
  （buddy-copilot 的 Agent）的上下文撑爆——`_symptom_row` 只按这份清单回填。
- 老命中记录里没存 `locate_by` 的，读取时按当前检测器声明现补（`resolve_locate_by`），
  所以改了这里，历史症状的定位依据也跟着更新。

## 没生效的声明去哪看

`GET /api/v1/admin/corpus/checks` 的 `load_errors` 列出所有**没能加载**的文件和原因。

一条检测器如果不在 `checks` 里，先看这里，不要以为是没命中。常见原因：YAML 结构坏了、
名字不在词表内、症状名重复。

## 派生特征（features.yaml）

`features.yaml` 里可以用原子词组合出命名特征，检测器的 `when` 里直接当名字用：

```yaml
features:
  is_main_prose: "zone == 'main' and not is_figure and not is_table"
  is_plain_main_text: "is_main_prose and not has_math"
```

```yaml
when: "is_plain_main_text and char_count <= 6 and matches(text, '^[（(][0-9]+[）)]$')"
```

同样是挂载生效，不用重建镜像。规则：

- 名字小写下划线，不能和原子词重名
- 可以引用别的派生特征，但**不能成环**（加载期报错）
- 一样禁止绝对页码、块 ID、具体正文文字
- 引用了块级字段的特征，在 `scope: page` 的检测器里用不了（加载期报错）

## 怎么加一个「眼睛」

「眼睛」= 一个可被检出的现象（例如「公式里黏进了正文」）。词表分四层，
**只有最上面两层住在镜像里**，其余全在这个挂载目录里，改完保存即生效：

| 层 | 住哪 | 是什么 | 加一个要不要重建镜像 |
|---|---|---|---|
| L0 事实字段 | 镜像 `app/services/corpus_symptoms.py` | 一块一页的原始事实（`zone`、`char_count`、`rel_y0`…） | 要（几乎不该动） |
| L1 算子 | 镜像 同上 | `matches` `contains` `any` `lexicon` `latex_texts`… | 要（小而稳定，基本齐了） |
| **L2 配置词** | **`vocabulary.yaml`** | 一条 = 名字 + 表达式 + 中文释义 + 正反例 | **不要** |
| **L2b 词插件** | **`vocab_ext/*.py`** | 一条表达式真写不出来时的受限 Python | **不要** |
| 派生特征 | `features.yaml` | 现有名字的纯组合 | 不要 |
| 检测器 | `*.yaml` | 这个目录里的 18 条声明 | 不要 |

**按这个顺序试**：能用 `features.yaml` 组合出来就别写新词；写不出来再写 L2 配置词；
表达式真表达不了（例如要跨行遍历表格单元格）才写 L2b 插件。走到最后一步之前，
不用改任何 Python。

### L2 配置词：`vocabulary.yaml`

```yaml
words:
  has_math:
    scope: block                    # block | page
    doc: 是否含公式：块本身是公式块，或正文里出现了 LaTeX 排版标记。
    expr: >
      type == 'equation'
      or any([contains(text, marker) for marker in lexicon('math_markers')])
    examples:                       # 必填，双极性
      positive:
        - '1^{\circ}=\frac{\pi}{180}'
        - {type: equation, text: ''}   # 要指定别的字段时写成映射
      negative:
        - '这一段是纯文字，没有任何公式标记'
```

规则（缺一项加载期失败，进 `load_errors`）：

- **必须有 `doc`**（中文释义）。Studio 的检测器卡片按它逐词解释判据；没有释义，
  判据在界面上就是一串看不懂的名字。
- **必须有正反例，且加载时真跑一遍**。这是这一层唯一的正确性保证：释义说它在判
  什么，正反例证明它真的在判那个。对不上就不加载，引用它的检测器于是当场报错——
  而不是拿一个谎言当前提，安静地永不命中。
- 名字小写下划线，不能和 L0 / L1 / 别的词 / 派生特征重名。
- 可以引用别的配置词与插件词，**不能成环**（加载期报错）。
- 和检测器同权：**禁止硬写具体正文文字**。中文词表放 `lexicons.yaml`。

### 中文词表：`lexicons.yaml`

跨书守卫不许在表达式里硬写中文正文，所以「例外词」这类数据单独放这里：

```yaml
lexicons:
  math_label_words:
    doc: LaTeX 公式里合法出现在 \text{…} 内的中文标签（推导对齐标签、证明用语、分段条件连接词）。
    words: [原式, 左边, 右边, 证明, 综上, 其中, 当, 时]
```

表达式里用 `lexicon('math_label_words')` 取（名字必须写字面量）。每份词表**必须有名字
和中文释义**，说清「收的是什么、不收什么」——否则半年后没人知道该往里加什么。

### L2b 词插件：`vocab_ext/*.py`

受限 Python（`RestrictedPython`），跑在 buddy-worker 进程里。写法与安全边界见
[`vocab_ext/README.md`](vocab_ext/README.md)，要点：

- 必须声明 `NAME` / `SCOPE` / `DOC` / `REQUIRES` / `EXAMPLES`，正反例同样加载期真跑。
- `REQUIRES` 是依赖契约：**没写进去的字段在 `fields` 里看不见**。
- 禁 import、禁下划线属性穿透、禁文件与网络；有调用次数与单块耗时上限；
  单个词抛异常只废掉这个词，不影响整页。
- 加载器只扫 `vocab_ext/` **顶层**，`vocab_ext/proposals/` 里的草稿永远不会被执行——
  未审稿件放那里。
- 这是**可执行代码**，不是声明文件：Agent 写它要过专属批准闸
  （`vocab.request_plugin_edit`，批准卡上带正反例自检结果），不与 YAML 共用一张卡。

## 可用词表

用 `GET /api/v1/admin/corpus/checks` 拿到当前完整词表：每个词带来源层级、中文释义、
`depends_on` / `used_by`、孤词标记与命中统计；`vocabulary.derived` 是当前可用的派生特征。

界面上标 `unused`（孤词）的词说明**这只眼睛白开了**：没有任何检测器会因为它命中。
下面是 L0 / L1 速查（L2 与 L2b 随时在变，看接口，别看这份清单）。

### 块级（scope: block）

几何：`has_bbox` `x0` `y0` `x1` `y1` `width` `height` `cx` `cy`
`rel_x0` `rel_y0` `rel_x1` `rel_y1` `width_ratio` `height_ratio` `is_wide` `is_full_width`

内容：`type` `mineru_type` `role` `zone` `text` `text_preview` `char_count`
`has_text` `has_caption` `caption` `level` `is_heading` `is_figure` `is_table`

`has_math` 与 `cjk_text_in_math` 现在是 **L2 配置词**，住在 `vocabulary.yaml` 里，
中文例外表在 `lexicons.yaml` 的 `math_label_words`。用法不变，但改它不再需要重建镜像。

判定来源：`confidence` `confidence_label` `rule_id` `rule_layer` `column_count` `column_method`

跨块：`prev_type` `next_type` `prev_zone` `next_zone` `same_zone_count`
`same_type_count` `dup_text_count` `overlap_count` `y_overlap_max`
`is_first_in_zone` `is_last_in_zone`

### 页级（两种 scope 都可用）

`page_no` `page_block_count` `page_text_len` `page_figure_count` `page_table_count`
`page_heading_count` `page_main_count` `page_aside_count` `page_low_conf_count`
`page_w` `page_h` `main_text_len` `html_len`

### 函数（L1 算子）

`count_type(t)` `count_zone(z)` `count_role(r)` `html_contains(s)` `html_count(s)`
`startswith(s, p)` `endswith(s, p)` `contains(s, p)` `matches(s, regex)`
`len` `abs` `min` `max` `round`

聚合与文本原语（配置词里最常用）：

- `any(列表)` / `all(列表)` — 配列表推导式用，例如
  `any([contains(text, m) for m in lexicon('math_markers')])`
- `findall(正则, 文本)` — 所有匹配片段
- `latex_texts(文本)` — 公式里每个 `\text{…}` 的内容
- `has_cjk(文本)` — 是否含汉字
- `lexicon('名字')` — 取 `lexicons.yaml` 里那份词表（名字必须写字面量）

表达式支持列表推导式与生成器表达式。

## 自验流程

1. 试跑：`POST /api/v1/admin/corpus/checks/dry-run`，给 `job_id` 和页码，
   看命中哪些页、证据是什么。不落库。
2. 回归：把正反例写进 `learning-buddy-api/tests/data/checks/`，跑
   `pytest tests/test_corpus_symptoms.py`。
3. 正例召回不到 100%、或反例有误报，回第 1 步改 `when`。

正反例文件是一页的快照，格式见 `tests/data/checks/header_in_main_pos.json`：

```json
{"page_no": 1, "html": "<article>...</article>", "blocks": [{"id": "b1", "...": "..."}]}
```

`examples.positive` / `examples.negative` 里写的是**相对 `learning-buddy-api/` 的路径**，
例如 `tests/data/checks/header_in_main_pos.json`。

没有反例的检测器等于没测过误报率，`test_every_declared_check_has_both_polarities`
会直接判失败。

配置词与插件的正反例不走这套文件快照，直接写在词的声明里（见上文），
加载时就跑；插件草稿可以用 `vocab.check_plugin` 或
`python -m app.services.vocab_plugin_check <文件>` 单独验。
