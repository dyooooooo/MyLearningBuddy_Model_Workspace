# 版式问题先改规则数据，不改 Python

分栏 / 侧栏（aside）/ 页眉判错时，**默认出口是规则数据文件，不是 `app/` 源码**。
决策层已经把「量页面」和「判归属」拆开了：几何测量在 Python，归属策略在 YAML。

## 三层规则（层叠顺序：base < edition < page）

| 层 | 文件 | 用途 | 代价 |
|----|------|------|------|
| base | `learning-buddy-api/configs/layout/base_rules.yaml` | 所有教材共享的硬不变量 | 白名单外，**不要改** |
| edition | `learning-buddy-api/configs/editions/<edition_id>/layout_rules.yaml` | 这本教材的版式策略 | 白名单内，改完**不用重建**、**不用发布** |
| page | `learning-buddy-api/configs/editions/<edition_id>/overrides/pages/NNNN.yaml` | 某一页的定点修正 | 白名单内，改完**不用重建**、**不用发布** |

同层内 `priority` 大者胜；跨层后来者胜。历史上被 35B / fusion 定稿过的块会带
`refined_by in ("35b", "fusion")` 而被 pin 住，页级 override 是唯一能压过这种历史 pin 的层
（导入主链已不再产生新的 35B 定稿，只有旧数据里还有）。

## 一条规则长什么样

```yaml
rules:
  - id: hujiao.right-margin-tip-rescue
    when: >
      has_bbox and not zone_pinned and baseline_zone == 'main'
      and right_of_main and width <= ${aside_max_width}
      and has_companion_left
    zone: aside_right
    confidence: 0.75
    priority: 500
    note: 主栏右外侧的窄块且同高有主栏伴生 —— 是旁注不是正文
```

`when` 可用的变量见 `learning-buddy-api/app/services/ir/layout/features.py`；
`${...}` 取当前教材 Edition Bundle 的 geometry 值，别把阈值写死。

## 规则要生效，必须落在生效面上

方案（Edition Bundle）里**只有两处**真正影响 MinerU 全书导入：

| 写到哪 | 影响导入吗 | 怎么生效 |
|--------|-----------|----------|
| `<edition>/layout_rules.yaml`、`overrides/pages/NNNN.yaml` | ✅ | bind-mount + mtime 缓存，改完 `corpus.rebuild_ir` 即时生效 |
| `<edition>/config.json` 的 `geometry` / `patterns` / `features` | ✅ | `editions.save_draft` 后**必须 publish** 才落盘生效 |
| `<edition>/policy.json` 的 `policy.entries` | ❌ **对本线无效** | 只在拼装大模型提示词时注入，而导入主链永不调用大模型 refine；它仅对 35B 线的 `b35.vision_layout` 槽位有效 |

**结论**：把规则写进 `policy.entries` 等于没写。要生效就写 `layout_rules.yaml` 或 page override。

## 成本阶梯（第二级是 Cursor，不是 35B）

```text
几何 + 规则  →  高置信度约九成页直接过
低置信度页  →  写入 job.meta.layout_escalate_pages → Cursor SDK 判定
```

导入主链（`import_ir_pipeline._run_finalize_streaming`）**永不**跑 35B refine。
拿到任务后先 `get_import_job`，读 `layout_escalate_pages` / `layout_escalate_summary`，
**优先处理这些页**。

## 分诊顺序（从便宜到昂贵）

0. **escalate 清单** → 按页号逐页分诊（见 `skills/page-diagnosis.md`）
1. **单页判错** → 写 `overrides/pages/NNNN.yaml`，再 `corpus.rebuild_ir(pages=[N])`
   （layout-only，合并更新 escalate）；或 `rerender_page`
2. **这本教材系统性判错** → 写该教材 `layout_rules.yaml`，再 `corpus.rebuild_ir`
3. **几何阈值不对**（主栏边缘、伴生宽度）→ `editions.save_draft` 改 `geometry.*`，并提醒用户 publish
4. **量得不对，不是判得不对**（feature 本身算错）→ 才轮到 `fs.request_edit` 改 Python，且必须带 pytest 证据

前三条都不用 `docker.rebuild`：`configs/` 是 bind-mount，改完 `rebuild_ir` 即可。

## 已封禁通道（代码还在，工具层已硬拦截）

`reprocess_page` 的 35B 系列 mode 在本场景**已被代码拦下**，不是「建议不用」：

| mode | 原行为 | 现状 |
|------|--------|------|
| `content_correct` | 35B 只改文字 / LaTeX | 封禁 |
| `layout_content` / `refine_35b` | 35B 版式 + 内容一起重认 | 封禁 |
| `layout_rebuild` | 35B 整页骨架重建 | 封禁 |

`reprocess_page` **仅允许** `rerender` / `restore_mineru` / `from_cloud` 三种；
但首选出口是 page override + `corpus.rebuild_ir`，或 `corpus.rerender_page`。
顽固页继续用改块 / 规则，不要试图绕回 35B。

## MinerU 禁区速记

涉及 MinerU 导入、分栏、侧栏、目录（TOC）、页眉的任何改动前，
**必须先 Read** `doc/mineru-corpus-pipeline-lessons.md`（问题模式 A–N 全表 + 已落地修改清单）。

- **侧栏禁止 `position:absolute`**：落位用文档流 `margin-top`（`--lb-gap`）。
- 含中日韩（CJK）文字的整段**禁止**当裸 LaTeX 处理；走 `normalize_plain` 拆正文 + 行内公式。
- 有列表编号（list marker）时禁止 `_looks_like_bare_latex`。
- 同角色多个页眉块按 bbox 合并，勿只保留第一条。
- 目录用 `type` / `pedagogy_role` 分流 + 专用渲染器，不当普通 table / text。
- 整书导入不走 35B refine；单页 35B 重识别是另一条线——不许混。

> 一句话原则：Cloud 字段要吃全，版式角色要分清，几何分栏要保守（伴生 + 文档流落位），
> 预览交互靠 data 属性，改完镜像要重建，改完规则要 `rebuild_ir`。

改完发现新坑：把模式补进 lessons 文档第 1 节表格，保持它是唯一活文档。

## 硬约束

- 规则 id 必须带层前缀（`base.` / `<edition>.` / `page<N>.`），便于在 `layout_rule_id` 里溯源。
- 每条规则写 `note`，说明它在救哪一类页面——没有 note 的规则等于没人能维护。
- 加规则必须同时在 `learning-buddy-api/tests/test_layout_rules.py` 或
  `test_layout_snapshot.py` 里留一个用例，否则视为未完成。
- 不确定该判主栏还是侧栏时，**不要猜**：保持低置信度进 escalate 清单，由 Cursor 写 page override。

## 批准是按路径的

`fs.request_edit` 批准的是**你申请的那几个路径**，不是整个白名单。
要改别的文件就重新申请；一轮改动的文件数 / 行数也有上限（见 `permissions.yaml` budgets），
超限整轮回滚。改动请一处一处来。
