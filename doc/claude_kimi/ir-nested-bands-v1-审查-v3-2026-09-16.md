# ir-nested-bands-v1 代码审查（v3 轮）

审查对象：`doc/ir-nested-bands-v1/` @ 2026-09-16 03:40（containment.py 1196 行 / render.py 451 行 / check.py 85 行 / build_canvas.py 1043 行 / paint.mjs 12 行）。
成品格式 `nested_bands_v3`。`python3 check.py` 实测 4/4 绿。
上一轮（v2）审查见 [ir-nested-bands-v1-审查-2026-09-16.md](ir-nested-bands-v1-审查-2026-09-16.md)。

---

## 一、总体评价

v3 是一次方向正确的「部分回摆」：v2 删掉的页级栏概念以**栏轨**（`page_tracks`，约 30 行、不扫白缝）的形式请回来，解决了 E 对照发现的「相邻带列边全不对齐」。机制与文档案例闭环：`absorb_above` 的「上方两列不吞」正好拦下化学 13 史话头；`_assign_members` 的「骑缝归正文」正好对应数学 11 练习左列。文档新增了「v2 删了什么 / v3 请回什么」两节决策记录，是这个仓库文档实践的高光。

上轮 9 项意见大部分已修（见第四节）。剩下的：一个修复没生效（chrome 文本）、一个修了一半（裁框）、check.py 覆盖面太薄、build_canvas.py 有几处脆点。

---

## 二、会造成错误输出的问题

### 2.1 chrome 文本修复是空操作【实测确认】

`assemble_page`（containment.py:1186-1193）想用 `_span_text(c, live)` 给 chrome 补字，但 chrome 块的子孙 `discarded=True`，`isolate_chrome` 已把它们排除出 `live`——查表注定落空。实测四页 algo 输出 chrome text 全 `""`。

- 修法：传 `boxes`（isolate 之前的全量列表），不是 `live`
- 现状被 `to_ir_page` 优先抄现行 ir/pages 的兜底盖住，但一旦兜底缺失页眉静默丢失

### 2.2 图面板不裁，生物 9 冒框【实测确认】

文字/标题本轮已裁（`_clip` 进 cell，越界记 `raw_union`/`crop`，化学 8 题头 470→379 ✓）。但 `nest_figure`（containment.py:942）明说「图身框不裁到栏轨」：

- 生物 9 实测：`panel_row` bbox `[73,175,528,645]`，带顶 221、cell 从 x206 起——**上冒 46px、左冒 133px**，和左侧空 aside 格的 grid 列重叠
- 不裁的先例是化学 13 左面板，但那是 span 带、面板本来就在 cell 内，裁不裁无区别；生物 9 是 pair 带，E 的口径恰恰是「进右轨**并裁**」
- 修法：面板裁到 **cell**（不是 track）——化学 13 无变化，生物 9 修好。y 方向同理（带顶已被 clip_to_siblings 推下来，part 不该再冒回去）

---

## 三、本轮新增机制的问题

### 3.1 check.py 覆盖面太薄

只比 tracks（frame/main/aside，±1/±8px）、aside_side、成员→轨归属。**不比**：带数与 kind 序列、parts（缩进/居中/题注）、part 是否冒出 cell——2.2 它就看不见。

建议加两条（都不需要金样扩容）：
1. 带的 kind 序列对比（chem13 金样已是 5 带，可直接比）
2. 自洽断言：所有 part bbox ⊆ cell bbox——无金样依赖，直接抓冒框

### 3.2 build_canvas.py 的脆点

- E groups 约 170 行是**内嵌冻结拷贝**（CHEM8_GROUPS 等）。页树是 `extract_page_json` 实时从原画布抽的（好），groups 不是——原画布一改就漂移，而画布注释还声称「整份拷来、没有另造简版」
- `extract_bio_tree` 靠正则抓 `TREE` + 伪造 `L()` helper 调 node 执行，源画布改 helper 签名就炸
- `/tmp/bio-tree.mjs` 固定文件名，应用 tempfile
- 它是本目录第一个**写目录外文件**的脚本：`~/.cursor/projects/...` 和 workspace canvases 双写，无注释说为什么两处，README 也没提

### 3.3 RENDER.md 缩水过度

182→59 行。三条坑级知识被删，现在只活在 render.py 注释里：
- parts 角色 → 树节点的映射表（blank/shared_caption/panel_row/row）
- 「一格多段必须套 cols→rows 两层，否则 rows 直接拼接、格子散进上层网格」
- shared_caption 为什么要新造 `kb-algo-cap-N` 块（src_i 会跟图身撞）

建议捡回来，哪怕以精简形式。

---

## 四、上轮意见的处理情况

| 上轮意见 | 状态 |
|---|---|
| parts 裁框 | **半修**：文字/标题 ✓，图面板 ✗（见 2.2） |
| `_set_caption` 空串覆盖 | ✓ 空串跳过 |
| `absorb_above` 链吞 | ✓ 三重保险（ABSORB_MAX_H=56 / 上方两列不吞 / 有并排邻居不吞） |
| 跨页父块不裁框 | ✓ `drop_cross_page` 裁到本页并收孩子并集 |
| 无金样对照 | ✓ check.py（但太薄，见 3.1） |
| 魔法数字 | ✓ 17 个常量带注释；`_figure_slot` 的 0.6/0.5、`_atomic_packs` 的 3 还散着 |
| 文档过时 | ✓ ALGORITHM/README 同步 v3；RENDER 缩水见 3.3 |
| 版本号不一致 | ✓ `source.nested_bands=FORMAT`；目录名仍叫 v1 |
| 画布下标错位 | ✓ 画布内嵌「下标怎么对」表 |
| 窄缝 4px 噪声 | ✓ `INNER_GAP=8`，gap 格子整体取消 |
| chrome 无字 | **修复未生效**（见 2.1） |
| `_x_clusters`/`from_image` 等死代码 | ✓ 已清 |

---

## 五、建议动手顺序

1. chrome 文本改传 `boxes`（2.1）——一行修复，但这次要验证 algo-*.json 里 text 非空
2. 面板裁到 cell（2.2）+ check.py 加 part⊆cell 自洽断言（3.1）——修和防一起走
3. check.py 加 kind 序列对比（3.1）
4. RENDER.md 捡回三条坑级知识（3.3）
5. build_canvas.py：groups 改实时抽取或标注「冻结拷贝」；双写处加注释（3.2）
6. 余量：use_fig 守卫条件去重、散落的 0.6/0.5/3 收常量、目录改名或 README 解释

---

## 附：v3 输出实测摘要

| 页 | 带数 | 栏轨（main / aside） | 备注 |
|---|---|---|---|
| 生物 9 | 3 | 206–535 / 70–173（左） | 图进右轨；面板冒框（2.2） |
| 化学 8 | 4 | 58–379 / 395–534（右） | 题头单独一条带，同行标题合一段 |
| 化学 13 | 5 | 214–535 / 73–201（左） | 史话头+正文分两带，与 E 五条占宽行一致 |
| 数学 11 | 3 | 196–541 / 67–198（左） | 练习整块进正文轨，提示框才占左轨 |

四页 chrome text 均空（2.1）。数学 11 带 0 成员序 `[1,2,3,4,6,5,7]`——跨列交错序，不是阅读序；members 顺序语义仍是没有声明的潜规则（阅读序在 parts 里）。
