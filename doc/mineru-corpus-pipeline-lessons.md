# MinerU Corpus 流水线：问题归纳与通用优化方法

> **范围**：`learning-buddy-c2` 上 **MinerU / corpus** 导入线（非 35B）。
>
> **样本教材**：沪教版高一数学上（job 例：`mineru_test` / `2617fd8c-...`）。
>
> **目的**：把已修过的同类问题抽象成可复用检查清单，换教材时按模式对症下药，而不是逐页特判。

**隔离提醒**（勿破坏）：

| 层 | MinerU (corpus) | 35B |
| --- | --- | --- |
| 队列 / MinIO | 默认 · `corpus/imports/` | `import35b` · `import35b/` |
| 代理 / UI | `/proxy/*` · `corpus.ts` · `MinerUPipelinePanel` | `/proxy35b/*` · `import35b.ts` · `B35PipelinePanel` |
| 渲染 | `document_ir` + `mineru_adapter` + `layout_enrich` | `import_35b/*`（可复用 CSS class，勿串逻辑） |

代码打进镜像：改 `learning-buddy-api/app/**` 后需 `docker compose build buddy-api buddy-worker` 再 `up -d`；`app/static` 为挂载。Studio 改动需重建 `c2-gateway`。

---

## 1. 总览：问题模式到抽象方法

| 编号 | 问题模式（抽象） | 典型症状 | 通用方法 |
| --- | --- | --- | --- |
| A | Schema 漂移 | Cloud/v2 有字段，HTML 无内容 | Adapter 按官方 schema 多路径取值；空 caption 回退 footnote |
| B | 语义类型误用 | 目录当 table、页眉被 skip；**VLM 目录当 text 被截成章标题** | 用 `type` / `pedagogy_role` 分流；`index` **与** text 启发式 TOC；专用渲染器 + CSS |
| C | Chrome 信息丢失 | 多段 header 只留一条 | 同角色多块按 bbox 合并，勿只保留 first |
| D | 版式交互缺失 | 目录不可点、页码无回跳 | data 属性 + 预览层事件；书页码不等于 PDF 页 |
| E | 分栏误判 | 主栏短行进侧栏；侧栏堆顶 | 宽正文定主栏；侧栏=外侧窄条 + 同高主栏伴生；落位用 margin 不用 absolute |
| F | 调试/性能 | 整书 JSON 卡死 UI | 页级切片 + 体积上限 + 懒加载 |
| G | 运维同步 | 改了代码预览仍旧 | 判断挂载 vs 镜像；全量 reprocess |
| H | 侧栏定位叠页 | 文字/图整页重叠不可读 | **禁止** `position:absolute` 摆侧栏；用文档流 `margin-top`（`--lb-gap`） |
| I | 整书预览后半空白 | 后半页要手动「重新渲染」 | 成功加载才标记 loaded；失败重试；并行拉页；HTML API **优先 draft chunk** |
| J | 编号例题「阶梯」错位 | (1)(2)(3) 看起来逐行右移 | 半角/全角括号混用 + 正文 `text-indent`；统一 `（N）`、去首尾空白；列表项用 `padding-left:2em`（与正文首行同宽、整行对齐），勿用累进空格 |
| K | 中文+裸 LaTeX 整段变公式 | 含 `\\leqslant` 的例句字体/间距异常 | **禁止**把含 CJK 的整段当 bare latex；`normalize_plain` 拆成正文+inline math |
| N | 编号项+行内公式粘行 | （3）（4）挤同一行 / 整段进 `math--inline` | 渲染前 `split_multi_numbered_items`；有 list marker 时禁止 `_looks_like_bare_latex`；走 `normalize_plain` 分段 |
| L | 「解」与题目/答案缩进 | 解顶格或凸出题目左侧 | 盒模型：解与题目同 `--lb-indent`；答案列 = 2×。**光学**：全角 `（` 有左侧 bearing，解墨迹贴左——仅给 `.lb-solution-label` 加 `--lb-marker-optical`（约 0.6em），勿再加整行 padding。Studio 内联 critical CSS + 打开任务强制重拉页 |
| M | 左侧页导航乱跳 | 点第 N 页却滚到别处 | 同 job 再点页**禁止** `startPreviewPoll` 清空重载；并行挂载须按页号插入 DOM；跳转先 `loadReadyPage(N)` 再 scroll |

换教材时：先对照 **PDF 原图 + Cloud content_list(_v2) + 出稿 HTML**，归入上表某一类，再改对应层（adapter / enrich / render / Studio）。

---

## 2. 已落地修改清单（按链路）

### 2.1 导入树与预览交互（Studio）

| 改动 | 文件 | 要点 |
| --- | --- | --- |
| 导入页点击进 MinerU 预览，不进教材编辑器 | `exam-ocr-server/studio-ui/.../SidePanel.ts` | `openImportJob(jobId, pageNo)` |
| 树节点带 `import_job_id` / `source_page` | `corpus_tree.py` · `schemas/corpus.py` | meta 可复现 |
| 全书导入 | MinerU Cloud → adapter → enrich → HTML | **不走 35B**（profile `refine_mode=skip`） |
| 单页「重新识别」 | 上次页 IR + 页图 → 35B `rerecognize` | **不走 MinerU**；`mode=refine_35b` |
| 单块点击重识别 | 该块 + 页图 → 35B | **不走 MinerU**；`…/blocks/{id}/refine` |
| 仅重渲 HTML | 存档 IR → enrich → HTML | `mode=rerender`（无 MinerU、无 35B） |
| 运维从 Cloud 重适配 | 落盘 content_list → adapter | `mode=from_cloud`（显式） |
| 页码悬停「返回目录页」 | 同上 + `document_ir` 渲染 | `title` + decorate 已出稿 HTML |

**通用方法**：预览层用 `data-*` 属性做导航，不要靠 `<a href>`；书页码与 PDF `page_no` 用 running `page_number` 建映射。

### 2.2 Cloud JSON 到 IR（adapter）

| 改动 | 文件 | 要点 |
| --- | --- | --- |
| v2 图路径 `content.image_source.path` | `mineru_adapter.py` · `mineru_cloud.py` | 勿只读顶层 `img_path` |
| `asset_prefix` 映射到 `.../mineru/foo.jpg` | rewrite 逻辑 | 与 MinIO 落盘一致 |
| `image_footnote` 并入 caption | `_extract_caption` | caption 空则用 footnote；两者都有则合并 |
| `index` / list：`list_items[].item_content` | `_list_item_text` · `_parse_toc_entry` | 行末数字=书页码；`level` / `is_chapter` |
| **VLM `text` 目录**（无 `index`） | `_looks_like_toc_text` · `_promote_vlm_toc_blocks` | 多行+行末页码 → `toc_entries`；**先于** `normalize_chapter_title`，避免整段被截成「第N章」；页内续块（裸章标题）在「目录」页提升 |
| refine 不降级 TOC | `apply_refine.py` | 已有 `pedagogy_role=toc` / `toc_entries` 时保留 |
| `page_header` / `page_number` 保留为 running | `_RUNNING_TYPES`，勿进 `_SKIP_TYPES` | `layout_zone=running` + `skip_render`，由页骨架渲染 |

**通用方法**：

```text
对每个 MinerU type：
  1. 查官方 content_list / content_list_v2 字段表
  2. 写「顶层 OR content.* OR spans」多路径提取
  3. 空值时定义回退链（caption <- footnote；text <- item_content）
  4. 单测钉死 v1/v2 各一份 fixture
```

### 2.3 页骨架与目录 HTML（render）

| 改动 | 文件 | 要点 |
| --- | --- | --- |
| TOC：title / leader / page | `document_ir._render_toc_html` | 显式 `span.lb-toc-leader` 夹在中间（勿把伪元素放在 page 后） |
| TOC 层级缩进 | `lb-toc-row--lv0/1/2` | 章 / 节 / 更细 |
| 多段 `page_header` 空格拼接 | `_pick_running_chrome` · `_render_running_header_html` | 按 bbox 排序后用空格 join |
| 页码左右栏 | `chrome_slot` 到 `lb-page-number--left/right` | 左页码勿写死靠右 margin |
| bleach 放行 | `title` / `data-goto-*` / `role` / `tabindex` | sanitize 白名单同步 |
| 编号例题对齐 | `normalize_block_text` · `polish_page_html` · `p.lb-list-item` | 去首尾空白；统一 `（N）`；`padding-left:2em` 对齐正文首行；整书可用 `rerender-page` 批量回写 draft |

**通用方法**：Flex 引导线必须 **DOM 顺序 = 视觉顺序**；伪元素插在错误位置会表现为「页码贴标题 + 底线」。

### 2.4 分栏与侧栏落位（layout）

| 改动 | 文件 | 要点 |
| --- | --- | --- |
| bbox 分栏检测 | `column_detect.py` | 宽正文估主栏边缘；侧栏=外侧窄条 + **同高度有主栏伴生** |
| 伴生 / span | `left_spans` / `right_spans` · `_has_side_companion` | 无同高宽块伴生 → 不建侧栏、不进 aside |
| 侧栏垂直落位 | `document_ir.py` · `textbook-hujiao.css` | `lb-aside-item` + `--lb-gap`（**margin-top 文档流**） |
| 并排网格 | `render_zone_grid_html` · CSS | `lb-grid--l/r/lr/m`；**空 aside 不渲染**；禁 `display:block` 叠栏 |
| bleach 保护 | `sanitize_page_html` | **整段**保护 `lb-aside-item`（勿只保开标签，否则闭标签被剥） |
| 接入 enrich + reprocess | `layout_enrich.py` · `corpus_reprocess_service.py` | 页级 reprocess 必跑 enrich |
| 页眉页码不参与 gutter | content boxes only | 避免页码假栏；chrome_slot 按 x 相对 main 回退 |
| 误判修复 | 练习/命题短行留 main | 宽块 `w>=380` 强制 main；无同高伴生 → main |

**失败案例（已修）**：

| 现象 | 根因 | 正确做法 |
| --- | --- | --- |
| 练习标题进左栏 | 短行不够宽，被直方图切成假 gutter | 主栏左缘=宽块 x0 高分位；仅 `x1 < main_x0 - gutter` 的窄块候选 |
| 短行仍像侧栏 | 只看水平空隙 | 再要求 **Y 与主栏宽块重叠**（并排岛） |
| 侧栏全堆栏顶 | 分栏后 aside 按 DOM 顺序顶对齐 | 按 bbox y 算与上一侧栏块的间隙 → `--lb-gap` margin-top |
| **整页文字/图重叠** | 曾用 `position:absolute` + `--lb-top%` | **禁止 absolute**；绝对定位脱离文档流会盖住主栏 |
| **主栏跑到侧栏下面（上下堆叠）** | `@media` 把 `.lb-grid` 改成 `display:block`；空左栏仍占一列；bleach 剥 aside-item 闭标签 | **禁止** grid→block；空 aside 不渲染；`lb-grid--l/r/lr/m`；整段保护 aside-item |

**通用方法**：

```text
1. 用「宽块」定义主栏，不用全块覆盖直方图直接切栏
2. 突出侧栏 = 水平离开主栏 + 同高度有主栏伴生（并排岛）
3. 宽块 / 无同高伴生的短行 -> 一律 main
4. 侧栏落位：同一 aside 列内按 y0 排序，margin-top = Δy 映射 em（--lb-gap）
5. 永不对侧栏内容使用 position:absolute / top%
6. 对照 PDF：人物/tip/旁注 vs 主栏起笔偏左的标题
```

Profile 软先验（`hujiao_math_hs_v1.json`）：`aside_left_x1_max` 约 280，`aside_right_x0_min` 约 680。

**相关单测**：`tests/test_column_detect.py`（伴生、orphan 左条、gap em）；`tests/test_document_ir_html.py`（`lb-aside-item` / `--lb-gap`）。

### 2.7 整书预览加载（Studio）

| 改动 | 文件 | 要点 |
| --- | --- | --- |
| 加载状态分离 | `MinerUPipelinePanel.ts` | `mounted` ≠ `loaded`；有 HTML 才算 loaded |
| 失败不丢弃 | 同上 | 404/空响应保留卡片为「等待出稿」，下轮重试 |
| 并行拉取 | 同上 | 并发 4；任务结束后多轮 catch-up 再停 poll |
| HTML API | `corpus_admin.get_import_page_html` | **优先 draft chunk**；无 chunk 再 IR 重渲染 |

**失败案例**：整书导入后后半页空白，要点「重新渲染」才出——因预览过早标记已渲染、串行 IR 重渲染超时后停 poll。


| 改动 | 要点 |
| --- | --- |
| Cloud layout/middle 页级切片 | 禁止整书 JSON 塞进 DOM |
| debug 体积上限 | `corpus_debug_bundle.py` |
| 页级 debug 落盘 | blocks / html / page_image / cloud 切片 |

**通用方法**：Studio 只拉 **当前页** 产物；书级文件只提供索引与按页 API。

### 2.6 运维

```bash
cd learning-buddy-c2
docker compose build buddy-api buddy-worker
docker compose up -d buddy-api buddy-worker
```

Studio TS 变更时另建 `c2-gateway`。

单页 / 全量重渲染（含 enrich）：

```text
POST /api/v1/admin/corpus/import/jobs/{id}/pages/{n}/reprocess
Body: {"mode":"from_blocks"}
```

验证：`docker exec buddy-api` 抽查文件与宿主机一致；预览硬刷新。

---

## 3. 换教材时的推荐流程

```text
1. 选 3～5 张难页：目录、有侧栏插图、有旁注、双页眉、公式密
2. 并排：PDF 原图 | Cloud content_list(_v2) | 出稿 HTML | debug/blocks
3. 归类到第 1 节模式 A～H
4. 只改对应层；加 golden / 单测（v2 fixture + 分栏 bbox 用例）
5. rebuild 受影响服务 -> 全量或抽样 reprocess -> 硬刷新对照
6. 把新 case 补进本文第 4 节「教材差异登记」与本节清单
```

验收口诀：

- **有则显**：Cloud 有的结构字段，HTML 要有对应块（图注、页眉、目录行）
- **位则准**：侧栏/主栏与原图一致；短行不因「不够宽」进侧栏；侧栏高度用 margin 对齐，不叠主栏
- **可导航**：目录到正文、页码到目录
- **可复现**：debug 页级可下载；acceptance/单测钉行为

---

## 4. 教材差异登记（持续追加）

| 教材 / Job | 新现象 | 归入模式 | 处理摘要 | 日期 |
| --- | --- | --- | --- | --- |
| 沪教高一上 / mineru_test | v2 图路径、footnote、多 header、假分栏、TOC 交互 | A～E | 见第 2 节 | 2026-07 |
| 沪教高一上 / mineru_test | 侧栏需同高伴生；落位勿堆顶；absolute 整页重叠 | E、H | 伴生检测 + `--lb-gap` margin；禁 absolute | 2026-07-10 |
| 沪教高一上 / mineru_test 60页分栏审计 | 漏侧栏 tip；章扉页标题误入左栏；主栏内双栏练习未建模 | E | 见下方「60页审计」；待修 | 2026-07-10 |
| 沪教高一上 / mineru_test | 主栏在侧栏下（p12 等）：grid 被 media 改 block + 空左栏 | E、H | 去掉 display:block；空 aside 省略；`lb-grid--r/l/lr` | 2026-07-10 |
| （下一本） |  |  |  |  |

### 60页分栏审计摘要（job `2617fd8c` / mineru_test）

**统计**：20/60 页检出侧栏；其余多为单栏正文/练习（合理）。

| 类 | 页 | 问题 |
| --- | --- | --- |
| 基本正确 | 9, 12, 14, 22, 34, 42, 53, 56, 57… | 康托/∞旁注/人物/多数 tip 进对侧栏 |
| 漏侧栏 | **16** | 原图右侧 3 个 tip，只进 1 个；另 2 个进了 main |
| 漏侧栏 | **25** | 原图左侧 3 tip，只进 2；中间「例7反证法」进 main |
| 漏侧栏 | **36** | 原图右侧 4 tip，只进 2 |
| 漏侧栏 | **50** | 原图右侧 1 tip 整段进 main，aside=0 |
| 假侧栏 | **30** | 章扉页大标题「等式与不等式」进 `aside_left`（应为装饰/主视觉，非旁注） |
| 落位偏差 | 11, 21, 41… | `--lb-gap` 近似高度，与 PDF 仍有偏差；小图标偶进侧栏 |
| 未建模 | 50 等练习区 | 主栏**内部**左右分题（练习双栏）≠ 页级 aside，当前不做 |

**审计产物**：`/tmp/mineru_col_audit/{audit.json,report.md,images/,html/}`（本机临时）。


---

## 5. 关键路径速查

| 职责 | 路径 |
| --- | --- |
| MinerU 到 IR | `learning-buddy-api/app/services/ir/mineru_adapter.py` |
| 分栏 | `.../ir/column_detect.py` · `.../ir/layout_enrich.py` |
| HTML 骨架 / TOC / chrome | `.../services/document_ir.py` |
| 页重渲染 | `.../services/corpus_reprocess_service.py` |
| 教材 CSS | `.../app/static/textbook-hujiao.css`（挂载） |
| Profile | `.../configs/textbook_profiles/hujiao_math_hs_v1.json` |
| Studio 预览 | `exam-ocr-server/studio-ui/src/components/import/MinerUPipelinePanel.ts` |
| 分栏单测 | `learning-buddy-api/tests/test_column_detect.py` |
| TOC/页眉单测 | `tests/test_document_ir_html.py` · `tests/test_mineru_adapter.py` |

---

## 6. 与 35B 线的关系

- **可共享**：`textbook-hujiao.css` 中的 `.lb-toc` / `.lb-page` / aside grid 视觉约定。
- **不可共享**：35B 的 `page_compiler` / refine prompt / `import35b` 队列与 MinIO 前缀。
- 同类「目录点线、分栏」若在 35B 出现，应在 **35B 自己的 normalize/render** 复用方法，不要跨线 import corpus 模块写队列。

---

## 7. 一句话原则

> Cloud 字段要吃全，版式角色要分清，几何分栏要保守（伴生+文档流落位），预览交互靠 data 属性，改完镜像要重建、整书要 reprocess。

---

## 8. Agent 使用约定

改 MinerU / corpus 流水线前 **先读本文**（Cursor rule：`.cursor/rules/mineru-corpus-pipeline.mdc`）。

改完若有新坑：补第 1 节模式表、第 2 节对应小节、第 4 节教材登记——保持本文为唯一活文档。
