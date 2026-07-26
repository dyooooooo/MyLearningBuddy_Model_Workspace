# MinerU Cloud + 35B 语料导入管线计划

> **原则**（2026-06-28 定稿）  
> 1. 能用 **MinerU Cloud** 的，就用 MinerU Cloud  
> 2. 能用 **35B**（`qwen36-35b-nvfp4` @ `vllm-qwen36-35b:8088`）的，就用 35B  
> 3. **自研**只做百分之百可预期的胶水（schema 映射、模板、库调用）；猜布局/猜语义一律不上主链  

> **数学渲染**：全链路 **MathJax 3**（后端 `math_normalize` + 前端/Studio SVG typeset），不再依赖 KaTeX。

---

## 1. 目标管线（唯一主链）

```text
PDF (MinIO)
  → MinerU Cloud 解析
       model_version=vlm · enable_formula=true · enable_table=true
       产出：content_list blocks（bbox / type / text / LaTeX 草稿 / 图路径）
  → 自研胶水（确定性）
       mineru_adapter · 页图 JPEG（PyMuPDF）· MinIO 插图 URL · source_manifest
  → 35B 逐页精修（多模态：页图 + MinerU blocks JSON）
       LaTeX 校正（MathJax 可渲染）
       zone：aside_left | main | aside_right | full_bleed | running
       语义块 chunks（block_ids 列表，供 RAG / 目录）
  → 自研 HTML 三栏模板（确定性）
       render_page_html + textbook-hujiao.css · data-latex 占位
  → Studio 预览 MathJax 3 typeset
  → corpus_tree_from_ir（优先用 35B chunks；章/节仍走确定性规则）
  → draft → review → Milvus
```

**唯一降级**：MinerU Cloud 失败（Token/超时/空 blocks）→ **35B 全页 Vision OCR**（现有 `pdf_scanned_ocr.py`），并在 `meta` 标记 `fallback_from=mineru_cloud`。

**禁止**：MinerU 成功后再走 35B 全页重 OCR（浪费且丢 bbox）；禁止多引擎投票猜对错。

---

## 2. 职责矩阵

| 环节 | 执行方 | 类型 | 说明 |
|------|--------|------|------|
| PDF 版面解析、bbox、表格、插图、公式 LaTeX **草稿** | **MinerU Cloud** | 外部 | Studio 已可配 `vlm` + formula/table |
| `content_list` → `DocumentBlock` IR | **自研** `mineru_adapter` | ✅ 确定性 | 类型映射、排序、skip discarded |
| 插图上传 MinIO + URL 回写 | **自研** `import_assets` | ✅ 确定性 | |
| 页图渲染（精修输入） | **自研** `render_page_pixmap` | ✅ 确定性 | 与 OCR 同 DPI 策略 |
| **zone**（左/中/右栏） | **35B** | 语义+视觉 | 替代 `layout_enrich._classify_zone` 的 bbox 阈值猜 |
| **pedagogy_role**（例题/注释/正文） | **35B** 为主 | 语义 | profile 正则仅作 **hint**，不作最终裁决 |
| **LaTeX 校正** → MathJax | **35B** + `math_normalize` | 半确定 | 35B 出稿；Node/MathJax bridge 校验 |
| 三栏 HTML 骨架、bleach、CSS | **自研** `document_ir` | ✅ 确定性 | 已有 `lb-grid` / `textbook-hujiao.css` |
| 公式 HTML 占位 `data-latex` | **自研** | ✅ 确定性 | 不在服务端嵌 SVG（与 Studio 一致） |
| Studio 公式渲染 | **MathJax 3** 前端 | ✅ 库 | `studio-ui` `math-render.ts` |
| **RAG 语义分块** | **35B** `chunks[]` | 语义 | 替代 `corpus_tree_from_ir` 600–1200 字窗口 |
| 章/节树（title/heading） | **MinerU 标题** + `is_chapter_text` 等 | ✅ 确定性 | 已有规则保留 |
| bbox 归一化到 0–1000 | **自研** | ✅ 确定性 | 纯算术 |
| `pdf_text_bbox_align` 模糊对齐 | **自研** | ⚠️ 非 100% | **仅 vision_ocr 降级路径**；MinerU 主链不用 |
| `layout_enrich` 图组 y 容差聚类 | **自研** | ⚠️ 启发式 | 可保留为 **35B 未覆盖时的兜底**；golden 页验收 |

---

## 3. 现状 vs 目标（代码落点）

| 模块 | 现状 | 目标动作 |
|------|------|----------|
| `ImportWizard` 默认引擎 | `vision_ocr` | 改为 **`mineru_cloud` 默认**（Token 未配时提示，不静默降级） |
| `import_ir_pipeline.build_ir_from_parse` | adapter → **全量** `layout_enrich` | adapter → **`mineru_page_refine`（35B）** → 瘦身后 `layout_enrich`（仅确定项） |
| `layout_enrich.py` | zone/role 靠 bbox+正则 | **删除 zone/role 主逻辑**；保留 normalize_bbox、mineru_type→running、figure_group 可选 |
| `mineru_page_refine.py` | **不存在** | **新增**；见 §4 JSON 契约 |
| `corpus_tree_from_ir.py` | 600–1200 字 merge | 有 `refine_chunks` 则直接用；否则最小确定性 atomic merge |
| `document_ir._render_equation_html` | `data-latex` 纯文本 | 导入后对 equation block 跑 `math_normalize`，写 validated latex |
| `pdf_scanned_ocr.py` | 与 MinerU 并列主链 | 仅 **MinerU 失败降级** |
| Harness | `textbook-layout-pages.json` 9/12/16 | 加 **LaTeX/MathJax** golden；`acceptance-corpus-import.sh` 卡 MinerU 主链 |

---

## 4. 35B 逐页精修契约（`mineru_page_refine`）

**输入（每页）**

- `page_jpeg`（base64 或已渲染 bytes）
- `mineru_blocks`：该页 `DocumentBlock` 列表（id、type、text、latex、bbox、mineru_type）
- `profile_id`：`hujiao_math_hs_v1`（prompt 注入栏位阈值说明，非硬编码裁决）

**输出（JSON Schema，Pydantic 校验，失败整页重试 1 次）**

```json
{
  "page_no": 12,
  "blocks": [
    {
      "id": "blk_…",
      "zone": "aside_right",
      "pedagogy_role": "note",
      "latex": "\\overline{A}",
      "latex_valid": true
    }
  ],
  "chunks": [
    {
      "chunk_id": "c1",
      "label": "1.2 集合的表示",
      "block_ids": ["blk_a", "blk_b"],
      "anchor": "1.2-repr"
    }
  ]
}
```

**约束**

- `zone` / `pedagogy_role` 枚举与 `document_ir` / profile 一致；非法值 → harness 失败  
- `latex` 仅允许 equation 类 block；须经 `math_normalize` 可解析  
- `block_ids` 必须覆盖该页全部非 discarded block，且不重不漏  
- Prompt 版本化：`prompts/mineru_page_refine_v1.md` + golden 回归  

**实现**

- 调用：`app/clients/vllm.py` 扩展 `vision_page_refine_json(image, blocks_json, system)`（与 `vision_page_to_markdown` 同模型）  
- 编排：`app/services/ir/mineru_page_refine.py`  
- 写入：`block.meta.layout_zone`、`block.meta.pedagogy_role`、可选 `block.text`/`latex` 覆盖  

---

## 5. MathJax 3 在导入链中的位置

```text
MinerU equation block (LaTeX 草稿，可能错)
  → 35B refine.latex（校正）
  → math_normalize（PluriMath + MathJax SVG 校验，segments/valid）
  → HTML: <span class="math" data-latex="…">…</span>
  → Studio: MathJax 3 typeset（已实现 math-render.ts）
```

**不做**：服务端批量嵌 SVG 进 DB（体积大、与 Studio 双份逻辑）。  
**要做**：`equation` 块入库前 `normalize_plain` / segment golden（修复 `q_complement` 等 bad case）。

---

## 6. 自研保留清单（白名单）

仅以下模块长期保留；其余启发式逐步退场：

| 模块 | 理由 |
|------|------|
| `mineru_adapter` | 纯 schema 映射 |
| `import_assets` / `source_manifest` | IO 胶水 |
| `document_ir` render + bleach | 模板与消毒 |
| `textbook_profile` JSON 加载 | 配置，非推理 |
| `math/normalize.py` + Node bridge | 库 + 确定性校验 |
| `corpus_import_incremental` 进度/分块写库 | 事务编排 |
| `render_page_pixmap` / `extract_pdf_bytes` | PyMuPDF 确定性 |

---

## 7. 验收（Harness）

| 项 | 路径 / 命令 |
|----|-------------|
| 布局三栏 | `data/eval/textbook-layout-pages.json` 页 9、12、16 |
| 导入 E2E | `scripts/acceptance-corpus-import.sh`（`parse_engine=mineru_cloud`） |
| 公式 | `tests/test_math_normalize.py` + 新增 equation golden |
| 35B refine | `tests/test_mineru_page_refine.py`（mock vLLM + 固定 JSON fixture） |
| Studio | 导入后 HTML 预览无裸 LaTeX；`mjx-container` 可见 |

---

## 8. 实施顺序（一次交付，不分阶段甩锅）

1. **`mineru_page_refine`**：prompt + schema + vLLM 调用 + 单测  
2. **接入 `import_ir_pipeline`**：MinerU 路径强制 refine；vision 路径暂跳过 refine  
3. **瘦身 `layout_enrich`**：去掉 zone/role 主链；保留 bbox 归一化  
4. **`corpus_tree_from_ir`**：消费 `chunks`  
5. **equation + math_normalize** 入库前校验  
6. **Studio 默认引擎** → `mineru_cloud`  
7. **Harness 补齐** 并重导 golden PDF  
8. **文档**：更新 `corpus-studio.plan.md` 双通路表（MinerU 主链 + 35B 降级）

---

## 9. 明确不做

- 用自研 regex/bbox 替代 MinerU 解析  
- 用 35B 全页 OCR 替代 MinerU（除降级）  
- 在 `exam-ocr` / C2 侧放 A⁺ 或 ③ 逻辑  
- 恢复 KaTeX  
- 改 DeepTutor  

---

## 10. 配置默认值（MinerU Cloud）

| 键 | 值 | 位置 |
|----|-----|------|
| `model_version` | `vlm` | `mineru_settings` / Studio |
| `enable_formula` | `true` | 同上 |
| `enable_table` | `true` | 同上 |
| `parse_engine` | `mineru_cloud` | 导入 API 默认（待改 `ImportWizard` + `ImportPdfRequest`） |

---

## 11. 未来目标态详图（MinerU / 35B / 自研 三色分工）

> **读法**：下表与流程中 **M** = MinerU Cloud · **L** = 35B · **S** = 自研（确定性胶水/模板/库）

### 11.1 唯一主链（未来）

```text
[S] PDF 上传 MinIO · 创建 import job
  ↓
[M] MinerU Cloud 解析（vlm + formula + table）
      产出：content_list[] + images/ + .md（.md 仅调试/降级参考）
  ↓
[S] mineru_adapter          — content_list → DocumentBlock IR（字段映射、排序、skip）
[S] import_assets           — images/ → MinIO URL 回写
[S] render_page_pixmap      — 每页 JPEG（供 35B 输入，MinerU 不提供整页图）
  ↓
[L] mineru_page_refine      — 每页：页图 + 该页 blocks JSON → zone / role / latex / chunks
[S] apply_refine_to_blocks  — 把 35B JSON 写回 block.meta / block.text（纯赋值，无推理）
[S] math_normalize          — equation 块 LaTeX 校验（库，非 LLM）
[S] layout_enrich_slim      — 仅：bbox→0–1000、running 标记、可选图组兜底
  ↓
[S] persist_job_ir          — blocks.json · source_manifest · 页 HTML draft
[S] document_ir.render      — 三栏 HTML 模板 + bleach + data-latex 占位
[S] Studio MathJax 3        — 前端 typeset
  ↓
[S] corpus_tree_from_ir     — 章/节：确定性规则；RAG chunk：优先 35B chunks[]
[S] PG draft → review → Milvus
```

**降级支路**（MinerU 失败时）：

```text
[L] pdf_scanned_ocr（35B 全页 Vision OCR）
[S] vision_pages_to_ir
[S] pdf_text_bbox_align     — 仅降级路径；主链删除
[L] mineru_page_refine      — 降级路径也应走 refine（无 MinerU bbox 时 35B 权重更高）
→ 同上 HTML / 合并
```

### 11.2 逐步职责表（未来每一环谁做）

| 步骤 | 负责方 | 模块/能力 | 输入 | 输出 |
|------|--------|-----------|------|------|
| 1 PDF 结构化解析 | **M** | MinerU vlm API | PDF bytes | content_list、bbox、type、text、table_html、公式草稿、插图路径 |
| 2 IR 映射 | **S** | `mineru_adapter` | content_list | `DocumentBlock[]`（id/type/page/bbox/text/…） |
| 3 资产入库 | **S** | `import_assets` | images/ | MinIO key + Studio proxy URL |
| 4 页图生成 | **S** | `render_page_pixmap` | PDF + page_no | JPEG bytes |
| 5 版面栏位 zone | **L** | `mineru_page_refine` | 页图 + blocks + profile hint | 每 block `layout_zone` |
| 6 教学角色 role | **L** | 同上 | 同上 | `pedagogy_role` |
| 7 边注锚点 | **L** | 同上（或 refine 输出 `anchor_block_id`） | 同上 | aside → main 锚定 |
| 8 公式 LaTeX | **M** 草稿 → **L** 校正 → **S** 校验 | MinerU text + 35B latex + `math_normalize` | 公式块 | MathJax 可渲染 `data-latex` |
| 9 多图一组 | **L** 优先 | refine 可输出 `figure_group_id` | 页内 figures | 图组 id |
| 10 bbox 归一化 | **S** | `normalize_bbox_to_1000` | 原始 bbox + 页尺寸 | 0–1000 坐标（供 harness/调试） |
| 11 三栏 HTML | **S** | `document_ir.render_page_html` | blocks + zone meta | HTML 字符串 |
| 12 公式渲染 | **S** + MathJax 库 | Studio `math-render.ts` | data-latex | mjx-container SVG |
| 13 章/节树 | **S**（规则）+ **M**（标题块） | `is_chapter_text` / MinerU title | title/heading 块 | section 节点 |
| 14 RAG 语义分块 | **L** | refine `chunks[]` | block_ids | corpus chunk 节点 |
| 15 进度/事务 | **S** | `corpus_import_incremental` | — | job 状态、页 draft |

### 11.3 自研逻辑逐项：未来保留 / 迁出 / 迁给谁

| 现有自研逻辑 | 现状问题 | 未来归属 | 能放进 MinerU？ | 能放进 35B？ | 建议 |
|--------------|----------|----------|-----------------|--------------|------|
| `mineru_content_list_to_ir` 类型映射 | 无 | **保留 S** | ❌ 产品 IR 契约 | ❌ | 纯 schema，必须自研 |
| skip page_number/footer/discarded | 无 | **保留 S** | ✅ 已用 MinerU `type` | ❌ | 继续信 MinerU type，不自己猜 |
| `_sort_mineru_blocks` 阅读顺序 | 用 bbox_y0 排序 | **保留 S**（排序）+ **M**（块序） | ⚠️ 若 MinerU 给 `index` 则优先用 | ❌ | 有官方序则用，无则 bbox 排序 |
| `normalize_bbox_to_1000` | 无 | **保留 S** | ❌ | ❌ | 纯算术 |
| `_classify_zone` bbox 阈值 680/280 | 易错 | **删除 → L** | ⚠️ 仅当 MinerU 将来输出 `layout_zone` 字段时可改 **M** | ✅ 主方案 | 35B+页图；监控 MinerU 是否新增版面标签 |
| `mineru_type=aside_text` → 右栏 | 左栏人物页会错 | **删除 → L** | ⚠️ MinerU 若区分 left/right aside 可部分 **M** | ✅ | 不单凭 type 写死右栏 |
| `_classify_pedagogy_role` 正则 | 猜语义 | **删除 → L** | ❌ | ✅ | profile 正则仅作 prompt hint |
| `_assign_figure_groups` y 容差 | 启发式 | **降级 S** 或 **L** | ⚠️ MinerU 多图是否同一 `group_id` 待查 | ✅ refine 输出 `figure_group_id` | 优先 35B；无输出再用 y 容差兜底 |
| `_assign_anchors` bbox overlap | 启发式 | **删除 → L** | ❌ | ✅ | refine 输出 `anchor_block_id` |
| `reading_order` 按 zone 桶重排 | 依赖错误 zone | **保留 S**（模板序） | ❌ | 依赖 L 的 zone | zone 对了，桶内仍用 bbox_y0 排序 |
| `render_page_html` 三栏模板 | 无 | **保留 S** | ❌ 产品 UI | ❌ | HTML/CSS 是伴学台契约 |
| `_merge_blocks_to_chunks` 600–1200 字 | 非语义 | **删除 → L** | ❌ | ✅ chunks[] | 无 refine chunks 时仅 atomic 兜底 |
| `is_chapter_text` / `is_lesson_text` | 较稳 | **保留 S** | ⚠️ MinerU title 类型已部分覆盖 | 可不必 | 标题规则确定性高，保留省 token |
| `pdf_text_bbox_align` | 模糊匹配 | **仅降级路径** | ❌ | ❌ 全页 OCR 无 bbox 时由 35B refine 补 zone | 主链移除 |
| `math_normalize` | 未接入导入链 | **保留 S** | ❌ | ❌ 35B 出稿后必须库校验 | PluriMath/MathJax 确定性 |
| MinIO / manifest / blocks.json | 无 | **保留 S** | ❌ | ❌ | 基础设施 |
| 全页 Vision OCR | 与 MinerU 并列 | **仅降级 L** | ❌ MinerU 失败时 | ✅ | 不替代 MinerU 成功路径 |

### 11.4 「自研能否塞进 MinerU / 35B」决策规则

```text
能否放进 MinerU？
  → MinerU 是否已有 API 字段或能力输出同等结果？
  → 是否是「PDF→结构化块」范畴（解析、bbox、表格、公式 OCR）？
  → 是 → 用 MinerU，自研只做映射
  → 否（伴学 IR、MinIO、HTML 模板、语料树、Harness）→ 永远自研

能否放进 35B？
  → 是否需要看整页视觉或理解语义（栏位、例题/注释、chunk 边界、公式校正）？
  → 是 → 35B 逐页 refine，输入必须包含：页图 + MinerU blocks（含 bbox 作 hint）
  → 否（加减乘除、JSON 映射、HTML 拼接）→ 不要放 35B

绝不能放进 35B / MinerU 的：
  → HTML 三栏渲染、bleach、MinIO、job 状态机、Milvus 发布
  → 违反「百分之百可预期」的：不要用自研 regex/bbox 替代已分配给 L/M 的职责
```

### 11.5 MinerU 输出在未来链中的利用率（目标）

| MinerU 字段/产物 | 未来消费者 | 说明 |
|------------------|------------|------|
| `bbox` | **L**（主）+ **S**（归一化存档） | 作 35B 提示，不再用 S 阈值定 zone |
| `type` / mineru_type | **S** adapter + **L** hint | skip/映射保留；栏位不单凭 type 写死 |
| `text` / `table_body` / `img_path` | **S** adapter → **L** refine 可改 text/latex | 草稿经 35B 校正 |
| `page_idx` | **S** | 页号 |
| `images/` 裁剪图 | **S** → MinIO | 不变 |
| 整页 PDF 视觉 | **S** 渲染 JPEG → **L** | MinerU 不提供，必须自研 PyMuPDF |
| `.md` 全书 | 调试 / 降级参考 | 非权威；权威仍是 blocks.json |
| MinerU 未来若增 `layout`/`reading_order` | 优先 **M** → 减少 **L** token | 持续跟踪 MinerU 版本说明 |

### 11.6 `layout_enrich` 未来瘦身后的自研范围（`layout_enrich_slim`）

**保留（S，确定性）：**

1. `normalize_bbox_to_1000` — 坐标归一化  
2. `mineru_type in (page_number, footer)` → `running` + `skip_render` — 信 MinerU 分类  
3. （可选兜底）`_assign_figure_groups` — 仅当 35B 未输出 `figure_group_id`  
4. 按 zone 桶 + `bbox_y0` 生成 `reading_order` — 模板阅读序，非语义推理  

**删除（迁 L）：**

1. `_classify_zone` 阈值  
2. `_classify_pedagogy_role` 正则裁决  
3. `_assign_anchors` overlap 启发式  

### 11.7 实施时自研新增（非推理）

| 新模块 | 类型 | 职责 |
|--------|------|------|
| `mineru_page_refine.py` | **S** 编排 + **L** 推理 | 调 vLLM、校验 JSON、重试 |
| `apply_refine_to_blocks.py` | **S** | Pydantic 校验后写字段 |
| `prompts/mineru_page_refine_v1.md` | **S** | prompt 版本化（Harness 回归） |
| `vllm.vision_page_refine_json` | **S** 客户端 | HTTP 多模态调用 |

**35B 不做的事（避免范围蔓延）：** 不写 HTML、不上传 MinIO、不决定 Milvus 索引策略、不替代 MinerU 做 PDF 解析。

